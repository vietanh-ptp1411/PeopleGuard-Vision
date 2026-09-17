# VisionGuard — cài đặt và chạy 24/7 trên máy khách

Có **hai** cách đưa phần mềm sang máy khác. Cách A là cách nên dùng.

---

# Cách A — Bộ cài một file (khuyên dùng)

Chép **`release\VisionGuard-Setup.exe`** (377 MB) sang máy khách rồi bấm đúp. Xong.

Máy khách **không cần cài Python, không cần mạng** — mọi thứ đã nằm trong file.

Bộ cài sẽ:

1. Hỏi thư mục cài (mặc định `C:\VisionGuard`)
2. Giải nén — **giữ nguyên `config`, `models`, `logs`, `events` nếu đã có**, nên cài đè
   lên một máy đã chạy sẽ không mất camera, vùng ROI hay địa chỉ PLC đã cấu hình
3. Tạo lối tắt ở Desktop và Start Menu
4. Đăng ký chạy tự động khi đăng nhập Windows
5. Chạy kiểm tra hệ thống và in kết quả ra màn hình

Sau khi cài:

| Việc | Cách làm |
|---|---|
| Chạy phần mềm | `VisionGuard.exe` (hoặc lối tắt Desktop) |
| Chạy có giám sát, tự bật lại | `VisionGuardLauncher.exe` |
| Kiểm tra hệ thống | `VisionGuard.exe --check` |
| Nhật ký | `logs\` |

### Vì sao không cài vào Program Files

Config, log, lịch sử sự kiện và clip video nằm **cạnh file exe**. Trong `Program Files`
tài khoản người dùng thường không có quyền ghi, phần mềm sẽ chạy nhưng không lưu được gì.
Vì vậy mặc định là `C:\VisionGuard`.

### GPU

Bộ cài kèm **torch bản CPU**. Lý do: bản CUDA nặng 4.4 GB so với 526 MB và vẫn đòi máy
đích có driver NVIDIA. Đo thực tế bản CPU đạt **14–18 fps**, trong khi bộ phát hiện đã
được giới hạn ở 12 fps — nên không thiệt gì.

Máy nào thật sự cần GPU thì dùng **Cách B** với `-Gpu`.

### Build lại bộ cài

```
build\venv\Scripts\python.exe build\make_installer.py
```

Mất khoảng 7 phút. Kết quả nằm ở `release\VisionGuard-Setup.exe`.

---

# Cách B — Cài từ mã nguồn

Dùng khi máy đích cần chạy YOLO trên GPU, hoặc khi bạn muốn sửa code tại chỗ.

## 1. Cài đặt

Chép **toàn bộ thư mục dự án** sang máy khách, rồi mở PowerShell tại thư mục đó:

```powershell
powershell -ExecutionPolicy Bypass -File tools\install.ps1
```

Nếu máy có card NVIDIA và bạn dùng chế độ **PC AI / YOLO**:

```powershell
powershell -ExecutionPolicy Bypass -File tools\install.ps1 -Gpu
```

Script sẽ: tìm Python ≥ 3.10 → tạo `.venv` riêng → cài thư viện → **kiểm tra hệ thống** →
đăng ký tác vụ tự chạy khi đăng nhập Windows (trễ 30 giây cho mạng kịp lên).

Thêm `-NoAutoStart` nếu chỉ muốn cài mà không tự chạy.

> **Quan trọng:** đây là ứng dụng có giao diện nên phải chạy trong phiên làm việc có màn
> hình. Để sau khi mất điện máy tự vào Windows rồi tự chạy phần mềm, cần bật **tự động
> đăng nhập** (`netplwiz`, bỏ tick "Users must enter a user name and password", hoặc dùng
> Sysinternals Autologon). Không bật thì phần mềm chỉ chạy sau khi có người đăng nhập.

## 2. Kiểm tra hệ thống

```
tools\check.bat
```

Kiểm 8 nhóm: Python, thư viện, quyền ghi thư mục, file cấu hình, model YOLO, **từng
camera**, PLC, dung lượng đĩa.

| Mã thoát | Nghĩa |
|---|---|
| 0 | Sẵn sàng, không cảnh báo |
| 2 | Chạy được, có cảnh báo (ví dụ PLC đang ở chế độ mô phỏng) |
| 1 | Có lỗi nặng, chưa chạy được |

## 3. Chạy

```
tools\start.bat
```

Đây cũng là lệnh mà tác vụ tự khởi động gọi. Nó làm ba việc:

1. **Chờ** — lúc máy vừa bật, PC sẵn sàng trước switch, camera và PLC. Preflight được thử
   lại trong 3 phút thay vì fail ngay lần đầu.
2. **Chạy** — gọi `main.py --autostart`, phần mềm tự bấm START sau 3 giây. Không cần ai ra
   bấm nút sau khi mất điện.
3. **Chạy lại** — nếu app tắt bất thường thì bật lại, với khoảng chờ tăng dần
   (5s → 10s → 30s → 1p → 2p → 5p) để một lỗi cố định không làm treo CPU. Đóng cửa sổ
   bằng tay (thoát mã 0) thì launcher dừng theo, không bật lại.

Nhật ký: `logs\launcher.log`.

## 4. Gỡ tự khởi động

```powershell
powershell -ExecutionPolicy Bypass -File tools\uninstall.ps1
```

Chỉ gỡ tác vụ; phần mềm và dữ liệu vẫn nguyên.

---

# Những gì đã làm để chạy được 24/7

## Đã đo, không đoán

Chạy liên tục 4 camera với YOLO, lấy mẫu mỗi 15 giây:

| Chỉ số | Sau 150 giây | Kết luận |
|---|---|---|
| RAM (RSS) | 1516 → 1521 MB | **Phẳng** — không rò rỉ |
| Số luồng | 92 → 87 | Giảm |
| Handle | 760 → 753 | Giảm |
| Đối tượng Python | 351.874 → 351.899 | Phẳng |

Không có rò rỉ bộ nhớ. Vấn đề thật nằm ở **CPU** và ở **đĩa**.

## Giới hạn tốc độ nhận dạng

YOLO vốn chạy nhanh hết mức card cho phép — đo được **119% một nhân**, mãi mãi. Bài toán
người-trong-vùng không cần 25 quyết định mỗi giây: ở 12 fps một người đi bộ chỉ dịch
khoảng 6 cm giữa hai khung hình, vẫn nằm gọn trong debounce phía sau.

Thêm `max_fps` (mặc định **12 khung/giây cho mỗi camera**, 0 = không giới hạn) trong tab
AI Model. Mỗi camera có mốc thời gian riêng nên giới hạn là *của từng camera*, không phải
chia nhau.

**119% → 90% một nhân** với 4 camera. Trên máy 8 nhân là khoảng 11% tổng CPU.

## Dọn dẹp tự động

Mọi thứ phần mềm sinh ra đều tăng vô hạn nếu không ai xoá. Một tuần thì không sao; một năm
thì đầy ổ và phần mềm dừng vì một lý do chẳng liên quan gì tới việc phát hiện người.

Hai loại hạn mức, vì chỉ một loại là chưa đủ:

- **Theo tuổi** — trường hợp bình thường, quá hạn thì xoá
- **Theo dung lượng** — lưới an toàn: nếu ghi hình nhiều bất thường thì xoá clip cũ nhất
  cho tới khi thư mục về dưới hạn mức, bất kể tuổi

| Loại | Mặc định |
|---|---|
| Clip video | 14 ngày **và** tối đa 20 GB |
| Ảnh snapshot | 30 ngày |
| File log | 30 ngày |
| Lịch sử sự kiện (SQLite) | 90 ngày **và** tối đa 200.000 dòng, có `VACUUM` để trả lại chỗ trống |

Chạy một lần lúc khởi động rồi mỗi 6 giờ, trên luồng riêng — xoá mười nghìn file cũng
không làm đứng giao diện.

Trước đây `backupCount=60` của bộ ghi log **không hề có tác dụng**, vì hàm xoay vòng tự
viết không đổi tên file cũ nên cơ chế xoá của Python không bao giờ khớp. File log tích luỹ
vĩnh viễn.

## Còn một điều nên biết

Trong một lần chạy hàng loạt, `multicam_ux` **segfault một lần**; chạy lại 5 lần đều sạch
nên chưa tái hiện được. Tôi đã bỏ các luồng ghi clip khi tính năng tắt để giảm bề mặt rủi
ro, nhưng **chưa xác định được nguyên nhân**. Nếu gặp app tự tắt lúc thoát, xem
`logs\launcher.log` — launcher sẽ ghi lại mã thoát và tự bật lại.

Và như mọi khi: đây là thiết bị giám sát, không phải thiết bị an toàn đạt chuẩn.
