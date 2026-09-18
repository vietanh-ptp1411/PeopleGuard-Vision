# Test camera thật — ghi chú mang theo khi ra hiện trường

Dùng cho buổi test đầu tiên với camera thật, cắm thẳng vào cổng LAN của laptop.

Thứ tự bên dưới là thứ tự **phải làm**, không phải danh sách để chọn. Mỗi bước có một
cách kiểm chứng riêng, nên khi hỏng thì biết ngay hỏng ở đâu chứ không phải đoán giữa
năm thứ cùng lúc.

---

## Bước 0 — Chuẩn bị khi còn Internet

Làm **trước** khi rút dây mạng đi cắm camera. Nếu laptop chỉ có một cổng LAN thì lúc
cắm camera vào là mất mạng dây; Wi-Fi vẫn dùng được, nhưng đừng để phải cài đặt giữa
chừng.

```powershell
git pull
powershell -ExecutionPolicy Bypass -File tools\install.ps1        # máy không có card NVIDIA
powershell -ExecutionPolicy Bypass -File tools\install.ps1 -Gpu   # máy có card NVIDIA
```

Kiểm tra cài xong:

```
tools\check.bat
```

Lúc này bài kiểm **sẽ báo `[FAIL] Camera 1 — no IP configured`**. Đúng như vậy: phần
mềm được giao ở trạng thái chưa cấu hình. Chỉ cần dòng Python, các thư viện và model
YOLO đã `[ OK ]` là đủ để đi tiếp.

---

## Bước 1 — Nguồn và dây

| Việc | Kiểm chứng |
|---|---|
| Cấp nguồn cho camera (12V DC hoặc PoE) | Đèn trên thân camera sáng |
| Dây mạng từ camera → cổng LAN laptop | **Đèn cổng LAN của laptop sáng/nháy** |

Đèn cổng LAN không sáng thì dừng ở đây — chưa có liên kết vật lý thì mọi bước sau đều
vô nghĩa. Đổi dây mạng khác trước khi nghi ngờ camera.

---

## Bước 2 — Tìm camera

```
python tools\tim-camera.py
```

**Camera mới xuất xưởng gần như chắc chắn nằm ở `192.168.1.64`**, bất kể laptop đang ở
dải nào. Nếu laptop ở `192.168.63.x` thì hai bên không trao đổi được một gói IP nào:
ping trượt, trình duyệt trượt, RTSP trượt — mà camera vẫn hoàn toàn bình thường.

Công cụ này hỏi bằng SADP (UDP multicast, đúng cách mà phần mềm SADP của Hikvision
dùng). Camera trả lời ở tầng 2 nên **vẫn thấy được dù khác dải mạng**. Nó cũng cho biết
camera đã kích hoạt hay chưa — điều mà quét IP không bao giờ nói cho bạn.

Ghi lại từ kết quả: `IPv4Address`, `IPv4SubnetMask`, `HttpPort`, `Activated`.

Nếu thấy dòng `>>> CAMERA CHUA KICH HOAT <<<` thì sang bước 4 trước.

Không thấy gì thì xem [bảng lỗi](#bảng-lỗi-thường-gặp) cuối trang.

---

## Bước 3 — Đưa laptop về cùng dải với camera

Giả sử camera ở `192.168.1.64`:

1. `Win + R` → `ncpa.cpl`
2. Chuột phải card **Ethernet** → Properties → *Internet Protocol Version 4 (TCP/IPv4)*
3. Chọn **Use the following IP address**:

   | Ô | Điền |
   |---|---|
   | IP address | `192.168.1.50` |
   | Subnet mask | `255.255.255.0` |
   | Default gateway | **để trống** |

Để trống gateway là có chủ ý: đây là mạng cụt chỉ có camera, khai báo gateway ở đây sẽ
khiến Windows tưởng đường ra Internet đi qua cổng này và làm hỏng luôn Wi-Fi.

Kiểm chứng:

```
ping 192.168.1.64
```

Có trả lời mới đi tiếp.

---

## Bước 4 — Kích hoạt camera (chỉ với camera mới)

Camera Hikvision mới **chưa có mật khẩu** và từ chối mọi thứ cho tới khi được kích hoạt.

1. Mở trình duyệt vào `http://192.168.1.64`
2. Đặt mật khẩu cho tài khoản `admin`
3. **Ghi mật khẩu ra chỗ an toàn** — không có đường lấy lại, chỉ còn cách reset cứng

> Đọc mục [Mật khẩu camera — tuyệt đối đừng commit](#mật-khẩu-camera--tuyệt-đối-đừng-commit)
> trước khi bạn `git add` bất cứ thứ gì sau buổi test.

Trong trang web camera, vào **Configuration → Network → Advanced → Integration Protocol**
và bật **ONVIF** nếu có — không bắt buộc cho RTSP nhưng sẽ cần nếu sau này đổi hướng.

---

## Bước 5 — Thử RTSP trước, chưa cần mở phần mềm

Tách bạch hai câu hỏi "camera có phát được luồng không" và "phần mềm có đọc được luồng
không". Trả lời câu đầu trước, bằng một dòng:

```powershell
.venv\Scripts\python.exe -c "import cv2; c=cv2.VideoCapture('rtsp://admin:MAT_KHAU@192.168.1.64:554/Streaming/Channels/101'); ok,f=c.read(); print('doc duoc khung hinh:', ok, f.shape if ok else '')"
```

- In ra `doc duoc khung hinh: True (1080, 1920, 3)` → camera và đường mạng đều tốt,
  sang bước 6.
- In ra `False` → vấn đề nằm ở camera/mạng/mật khẩu, **chưa phải ở phần mềm**. Xem bảng lỗi.

Luồng chính là `/Streaming/Channels/101`. Luồng phụ nhẹ hơn nhiều là
`/Streaming/Channels/102` — nếu máy yếu hoặc hình giật thì dùng luồng phụ.

---

## Bước 6 — Cấu hình trong phần mềm

Mở `VisionGuard.exe` (hoặc `tools\start.bat`), sang tab **Camera**:

| Ô | Chọn / điền |
|---|---|
| Detection source | `PC AI / YOLO (software detects)` |
| Số camera | `1` (test một cái trước đã) |
| Camera Type | `RTSP / IP Camera` |
| Vendor preset | `Hikvision` — tự điền Path và Port |
| IP | `192.168.1.64` |
| Username | `admin` |
| Password | mật khẩu đặt ở bước 4 |
| RTSP URL | **để trống** |

Ô **RTSP URL** nếu điền sẽ **ghi đè toàn bộ** các ô IP/Username/Password/Path phía trên.
Đó là lối thoát cho camera lạ, không phải chỗ để điền thêm cho chắc.

Bấm **Test Connection**. Dòng `URL:` hiện bên dưới luôn che mật khẩu — đó là cố ý, mật
khẩu không được xuất hiện ở bất kỳ đâu trong giao diện hay nhật ký.

Sang tab **Zones**, vẽ vùng cần canh. Không có vùng nào thì phần mềm không báo gì cả.

Tab **PLC**: **để nguyên `Simulation`** trong buổi test này. Chỉ bỏ tích khi đã có PLC
thật đấu vào và đã soát lại bảng địa chỉ.

> Tab **AI Events** sẽ không hiện trong chế độ này — nó chỉ dành cho loại camera tự
> phát hiện người và tự gửi sự kiện về. Ở chế độ `PC AI / YOLO` thì máy tính nhận dạng,
> nên mọi thứ cần xem đều nằm ở tab **Overview**.

---

## Bước 7 — Chạy và quan sát

Phần mềm tự nạp model và tự chạy ngay khi mở, không cần bấm START.

Đứng vào vùng đã vẽ và kiểm ba thứ:

| Dấu hiệu | Ngoài vùng | Trong vùng |
|---|---|---|
| Khung bao quanh người | **xanh lá** | **đỏ** |
| Viền vùng đã vẽ | **vàng** | **đỏ** |
| Dòng chữ lớn ở tab Overview | `AREA CLEAR` | `PERSON DETECTED` |

Khung đổi từ xanh sang đỏ là dấu hiệu đáng tin nhất: nó cho biết phần mềm **vừa nhìn
thấy người, vừa xác định người đó nằm trong vùng**. Nếu khung vẫn xanh khi bạn đã đứng
trong vùng thì vùng vẽ sai chứ không phải nhận dạng sai.

Người bị bỏ qua (ngoài mọi vùng canh, hoặc trong vùng loại trừ) hiện khung **xám**.

Rời khỏi vùng thì phải trở về `AREA CLEAR`. Giao diện PLC hiện chỉ còn hai thiết bị:
`M100` = có người, `M110` = nhịp tim.

---

## Bước 8 — Ghi lại để mang về

Những số này quyết định cấu hình thật, đo một lần ở hiện trường còn hơn đoán mười lần ở nhà:

| Mục | Ghi |
|---|---|
| Khoảng cách camera → vùng canh (m) | |
| Độ cao lắp camera (m) | |
| Góc nghiêng (thẳng đứng / chếch / ngang tầm mắt) | |
| Ánh sáng (đèn xưởng / ngược sáng / ban đêm) | |
| FPS hiển thị trên thanh trạng thái | |
| Độ trễ từ lúc bước vào đến lúc hiện `PERSON DETECTED` | |
| Có lần nào sót người không, trong hoàn cảnh nào | |
| Có lần nào báo nhầm không, do vật gì | |
| Người mặc đồ bảo hộ / đội mũ có bị sót không | |

Mục "góc nghiêng" quan trọng hơn vẻ ngoài của nó: model được huấn luyện chủ yếu trên
ảnh chụp ngang tầm mắt, nên camera gắn cao chiếu thẳng xuống đỉnh đầu là trường hợp khó
nhất. Nếu có sót người, ghi lại độ cao và góc — đó là dữ kiện để quyết định đổi model
hay đổi vị trí lắp.

---

## Mật khẩu camera — tuyệt đối đừng commit

`config/camera_config.json` **đang nằm trong git**, và mật khẩu camera lưu trong đó
**dạng chữ thường, không mã hoá**. Sau buổi test, nếu bạn `git add -A` rồi push thì mật
khẩu camera lên git và **nằm lại trong lịch sử vĩnh viễn**, xoá file sau cũng không gỡ được.

Trước mỗi lần commit:

```powershell
git status                       # xem đúng những file mình sửa
git restore config/              # bỏ mọi thay đổi trong config trước khi commit
```

Nếu vẫn muốn commit thứ khác cùng lúc, soát lại trước khi push:

```powershell
git diff --cached | findstr /i password
```

Có dòng nào hiện ra là **dừng**, đừng push.

Muốn giữ cấu hình camera thật trên laptop mà không lo lỡ tay:

```powershell
git update-index --skip-worktree config/camera_config.json
```

Từ đó git sẽ bỏ qua mọi thay đổi trong file này. Gỡ lại bằng `--no-skip-worktree`.

---

## Bảng lỗi thường gặp

| Hiện tượng | Nguyên nhân hay gặp nhất | Xử lý |
|---|---|---|
| `tim-camera.py` không thấy gì | Windows Firewall chặn UDP 37020 vào | Tắt firewall cho mạng hiện tại, chạy lại |
| | Camera chưa lên nguồn, hoặc dây hỏng | Kiểm đèn cổng LAN trước |
| | Camera không phải Hikvision | Chạy `--quet 192.168.1` và `--quet <dải của laptop>` |
| Thấy camera nhưng `ping` trượt | Khác dải mạng | Bước 3 |
| Web vào được, RTSP từ chối | Camera chưa kích hoạt | Bước 4 |
| | Sai mật khẩu, hoặc mật khẩu có ký tự đặc biệt | Thử mật khẩu chỉ gồm chữ và số |
| | Sai đường dẫn luồng | Dùng đúng preset `Hikvision` |
| RTSP đọc được nhưng phần mềm không hiện hình | `Camera Type` không phải `RTSP / IP Camera` | Sửa ở tab Camera |
| | Ô `RTSP URL` đang có sẵn nội dung cũ | Xoá trống ô đó |
| Hình giật, trễ | Luồng chính quá nặng cho máy | Đổi Path sang `/Streaming/Channels/102` |
| Mất kết nối rồi tự nối lại | Bình thường — đã có sẵn cơ chế tự nối lại | Chỉ cần ghi lại tần suất |
| Có người mà không báo | Camera chiếu thẳng từ trên xuống | Ghi lại độ cao và góc ở bước 8 |
| | Vùng canh vẽ chưa trùm chỗ người đứng | Xem lại tab Zones |

---

## Một điều không được quên

Phần mềm này **không phải thiết bị an toàn được chứng nhận**. Nó không thay thế rào
chắn, nút dừng khẩn cấp, màn quang hay cảm biến an toàn đạt chuẩn. Buổi test này để đo
xem nó nhìn thấy gì, không phải để kết luận rằng có thể giao tính mạng cho nó.
