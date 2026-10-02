# VisionGuard GPU — hướng dẫn cài lên máy khách

Bản rút gọn để mang theo khi đi lắp. Giải thích đầy đủ nằm ở **`HUONG-DAN.md`** cùng thư mục.

---

## 0. Kiểm tra trước khi rời xưởng

Làm **trước** khi ra hiện trường. Thiếu một trong bốn thứ này thì ra tới nơi mới phát hiện
là mất cả buổi.

| Kiểm tra | Cách xem | Không đạt thì |
|---|---|---|
| **Card NVIDIA** | `dxdiag` → tab Display | Vẫn chạy được nhưng rơi về CPU, chậm |
| **Driver NVIDIA** | Bảng NVIDIA Control Panel → System Information | Cập nhật driver (cần bản 2025 trở đi) |
| **Smart App Control** | Windows Security → App & browser control | **Phải tắt** — xem mục ⚠️ bên dưới |
| **Ổ đĩa trống** | Explorer | Cần ≥ 20 GB cho phần mềm |

### ⚠️ Smart App Control — hỏi khách trước

Nếu máy khách bật **Smart App Control** (chỉ có trên Windows 11), Windows sẽ **chặn DLL của
PyTorch** và phần mềm không chạy được, báo lỗi `WinError 4551`. Nhìn y như lỗi cài đặt hỏng,
cài lại bao nhiêu lần cũng không khỏi.

Cách duy nhất là tắt nó: **Windows Security → App & browser control → Smart App Control → Off**.

> **Windows không cho bật lại.** Muốn bật lại phải cài lại Windows. Đây là thay đổi hệ thống
> không hoàn tác được, **phải hỏi ý khách trước khi làm**.

Kiểm tra nhanh bằng PowerShell:

```powershell
(Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\CI\Policy').VerifiedAndReputablePolicyState
```

`0` = đã tắt (chạy được) · `1` = đang bật (sẽ chặn) · `2` = chế độ đánh giá

---

## 1. Cài đặt

1. Chép **`VisionGuard-GPU.zip`** sang máy khách
2. Giải nén vào thư mục **ghi được** — ví dụ `C:\VisionGuard`

   > **Đừng đặt trong `C:\Program Files`.** Phần mềm ghi config, log, ảnh và video ngay cạnh
   > file exe, mà tài khoản thường không có quyền ghi vào Program Files.

3. Vào thư mục vừa giải nén, bấm đúp **`Install.bat`**

`Install.bat` tạo lối tắt Desktop và Start Menu, đăng ký chạy tự động khi đăng nhập Windows,
rồi chạy kiểm tra hệ thống.

Tác vụ tự chạy không thêm thời gian chờ sau đăng nhập và dùng `--skip-preflight` để
mở cửa sổ trước khi camera/PLC sẵn sàng. Ứng dụng tự kết nối lại thiết bị; model YOLO
vẫn cần thời gian nạp trước khi nhận diện được.

Máy đã cài bản cũ: chỉ cần chép file **`Install.bat` mới** vào thư mục chứa
`VisionGuardLauncher.exe`, rồi chạy lại để cập nhật tác vụ và lối tắt Startup.
Không cần chép đè cấu hình hoặc giải nén lại cả gói. Có thể kiểm tra thủ công bằng
`VisionGuardLauncher.exe --check-only`.

**Lần chạy đầu sẽ báo `[FAIL]` ở phần camera — đúng như vậy.** Phần mềm được giao ở trạng
thái chưa cấu hình: chưa có IP camera, chưa có vùng giám sát, PLC để chế độ mô phỏng. Chỉ
cần các dòng Python, thư viện và model đã `[ OK ]` là cài đặt thành công.

### ⚠️ Nâng cấp lên máy ĐÃ cấu hình — đừng giải nén đè

Trong gói có thư mục **`config\`** ở trạng thái trắng. Giải nén đè lên bản đang chạy là
**xoá sạch cấu hình của khách**: IP camera, tài khoản, IP PLC, địa chỉ device, và cả các
vùng giám sát đã vẽ. Ra hiện trường lần hai chỉ để nhập lại từ đầu.

Làm thế này thay vì giải nén đè:

1. Giải nén gói mới vào **thư mục MỚI**, ví dụ `C:\VisionGuard-new`
2. Chép **đè** 4 file cấu hình từ bản cũ sang bản mới:

   ```
   copy "C:\VisionGuard\config\*.json"  "C:\VisionGuard-new\config\"
   ```

   (`camera_config.json`, `ai_config.json`, `plc_config.json`, `app_config.json`,
   và `roi_config.json` nếu đã vẽ vùng)

3. Chép luôn `events\` sang nếu khách muốn giữ sổ sự kiện và ảnh cũ
4. Bấm đúp **`Install.bat`** trong thư mục mới — nó trỏ lại lối tắt và tác vụ tự khởi động
   sang thư mục mới
5. Chạy thử, thấy chạy đúng thì mới xoá thư mục cũ

Bản cũ còn nguyên cho tới bước 5, nên hỏng ở đâu cũng quay lại được ngay.

> Cấu hình bản mới đọc được cấu hình bản cũ: khoá lạ bị bỏ qua, khoá thiếu lấy mặc định.
> Không phải sửa file tay khi lên đời.

---

## 2. Cấu hình — làm đúng thứ tự này

Mỗi bước có cách kiểm chứng riêng. Làm đúng thứ tự thì khi hỏng biết ngay hỏng ở đâu, thay
vì đoán giữa năm thứ cùng lúc.

### Bước 1 — Mạng

Đưa máy tính và camera về **cùng dải mạng**. Camera Hikvision mới xuất xưởng gần như chắc
chắn ở `192.168.1.64`.

```
ping 192.168.1.64
```

Không thông thì dừng ở đây. Đổi IP máy tính về cùng dải (`ncpa.cpl` → Ethernet → Properties
→ IPv4 → dùng IP tĩnh `192.168.1.50`, mask `255.255.255.0`).

### Bước 2 — Camera

Tab **Camera**: chọn loại `rtsp`, điền IP, **port 554**, tài khoản và mật khẩu.

> ⚠️ **Port 554, không phải 8000.** Port 8000 là cổng SDK riêng của Hikvision — nó **vẫn nhận
> kết nối TCP** nên mọi phép thử "port có mở không" đều báo OK, nhưng nó không nói RTSP. Kết
> quả là phần mềm treo chờ rồi timeout, mà mọi thứ trông vẫn đúng. Đây là lỗi mất nhiều thời
> gian nhất khi lắp.

Bấm **Connect**. Thấy hình là xong bước này.

### Bước 3 — Vùng giám sát

Tab **Zones**: bấm **+ ROI**, vẽ polygon, chọn ROI rồi nhập **PLC bit** riêng,
bấm **Apply to ROI** và **Save ROI**. Ví dụ `ROI_001 → M200`, `ROI_002 → M201`.
Mỗi ROI dùng một bit không trùng ROI khác hoặc heartbeat. Người vào vùng nào thì bit vùng đó bật.

Camera lắp chéo thì vùng vuông ngoài đời sẽ thành **hình thang** trên ảnh — vẽ theo đúng hình
thang đó, đừng vẽ chữ nhật.

### Bước 4 — Model

Tab **AI Model**: mặc định `model1.pt`, imgsz `1280`. Bấm **Load model**, đợi khoảng 5 giây.

> **Đừng tick ô FP16 on CUDA** nếu máy dùng card GTX 16xx — đo được **chậm gấp 3 lần**, không
> nhanh hơn. Card RTX thì có lợi, nhưng phải đo lại trên đúng máy đó.

Kiểm tra `off delay` **để 500–1000 ms**. Đặt ngắn hơn sẽ làm tín hiệu PLC nhấp nháy.

**Containment mode** — phần nào của người phải vào vùng mới tính. Mặc định giao đi là
`Any Overlap`: khung người **chạm vùng là báo**, nhạy nhất, báo sớm nhất.

| Chế độ | Báo khi | Dùng khi |
|---|---|---|
| `Any Overlap` | khung người chạm vùng | mặc định — vùng quanh máy nguy hiểm |
| `Intersection %` | phủ đủ % diện tích (ô ngưỡng ở Advanced) | Any Overlap báo nhầm nhiều |
| `Center Point` | tâm khung vào vùng | |
| `Foot Point` | **bàn chân** vào vùng | chỉ quan tâm người đứng hẳn trong vùng |

> Any Overlap báo sớm hơn nhưng cũng báo nhiều hơn: khung người là **hình chữ nhật**, nên
> người đi sát mép vùng có thể làm khung quét trúng vùng. Với hệ an toàn thì đánh đổi này
> đúng chiều — báo sớm còn hơn bỏ sót. Nhiễu quá thì hạ về `Intersection 10%`.

### Bước 5 — PLC

Tab **PLC**: điền IP và port PLC. Gán bit từng ROI tại tab **Zones**; heartbeat mặc định `M110`.

> Trước khi thử: trong GX Works phải **tick "Enable write at RUN time"** ở tham số
> Ethernet/MC. Không tick thì PLC nhận kết nối bình thường nhưng **từ chối mọi lệnh ghi**,
> trả về mã `0x0055`, và phần mềm không ghi được gì cả.

Sang tab con **Manual I/O**, bấm **Write ON (1)** cho bit đã gán (ví dụ `M200`), rồi mở GX Works xem bit đó có
lên 1 không.

- Lên → đường mạng, giao thức, địa chỉ đều đúng
- Không lên → lỗi ở tầng PLC, chưa cần động tới camera

> ⚠️ **Chỉ dùng Manual I/O khi đã bấm STOP** và máy đang ở chế độ bảo trì. Bạn đang bật/tắt
> trực tiếp bit điều khiển máy. Thử xong nhớ STOP rồi START lại.

Xong thì **bỏ tick PLC SIM** để chuyển sang PLC thật.

### Bước 6 — Nghiệm thu

Về tab **Overview**, bấm **START** (nút nằm ở đáy tab này; hoặc phím **F5**), cho người đi
vào vùng, kiểm tra đủ ba thứ:

- [ ] Khung đỏ `PERSON IN AREA` hiện lên
- [ ] `M100` lên 1 trên PLC
- [ ] Người ra khỏi vùng → `M100` về 0 sau khoảng 1 giây

---

## 3. Bàn giao

- [ ] Bật **auto-logon** cho Windows (`netplwiz`) — mất điện xong máy tự vào Windows rồi phần
      mềm tự chạy. Không có bước này thì sau cúp điện máy dừng ở màn hình đăng nhập.
- [ ] Khởi động lại máy, xác nhận phần mềm tự lên và tự vào giám sát
- [ ] **Rút cáp mạng camera khoảng 30 giây rồi cắm lại** — phần mềm phải tự vào hình, không
      cần bấm gì và không cần khởi động lại. Nếu phải khởi động lại mới được thì báo lại.
- [ ] Ghi lại cho khách: IP camera, tài khoản camera, IP PLC, địa chỉ device đã dùng
- [ ] Dặn khách vị trí file log: `logs\<ngày>.log`

---

## Bảng lỗi thường gặp

| Hiện tượng | Nguyên nhân hay gặp nhất |
|---|---|
| `WinError 4551` khi khởi động | Smart App Control đang bật — xem mục 0 |
| Camera không kết nối | Dùng port 8000 thay vì **554** |
| Camera không kết nối, port đúng | Máy tính khác dải mạng với camera |
| "Đang kết nối lại..." mãi không vào | **Xem lý do ở thanh vàng** trên đầu màn hình — nó ghi rõ vì sao, và log `logs\<ngày>.log` cũng ghi từng lần thử |
| Camera công nghiệp không vào | Ô **Serial Number** điền sai — để trống = dùng camera đầu tiên |
| Có hình nhưng không báo người | Chưa vẽ vùng, hoặc chưa bấm Load model |
| PLC không nhận tín hiệu | Chưa khai kết nối SLMP trong GX Works |
| PLC báo `end code 0x0055` | PLC **từ chối ghi khi đang RUN**. Vào GX Works, tham số Ethernet/MC, **tick "Enable write at RUN time"** (cho phép ghi khi CPU đang RUN) rồi nạp lại tham số. Kết nối TCP vẫn tốt — PLC trả lời hẳn hoi và nói không. |
| PLC nối rồi ngắt liên tục | Gần như luôn là 0x0055 ở trên — xem dòng trạng thái ở tab PLC để biết mã lỗi |
| PLC nhấp nháy liên tục | `off delay` đặt quá ngắn — để 500–1000 ms |
| Chạy chậm, GPU không được dùng | Driver NVIDIA quá cũ, hoặc đã lỡ tick FP16 trên card GTX |
| Người ở xa không bị phát hiện | Hạ imgsz quá thấp, hoặc camera đặt quá xa/góc quá rộng |
| Người trước cửa sổ không bị phát hiện | Ngược sáng — bật **WDR** trên web camera |

---

## Nhắc lại về an toàn

Đây là hệ thống **giám sát bằng thị giác máy**, **không phải** thiết bị an toàn đạt chuẩn.
Nếu dùng để bảo vệ người khỏi máy móc nguy hiểm, hệ thống thực tế **bắt buộc** phải có safety
PLC và cảm biến an toàn đạt chuẩn (ISO 13849, IEC 62061, ISO 13855). Lớp AI chỉ là lớp phát
hiện bổ trợ.
