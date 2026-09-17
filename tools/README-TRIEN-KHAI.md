# VisionGuard — cài đặt và chạy 24/7 trên máy khách

Có hai gói sẵn trong `release\`:

| Gói | Dung lượng | Dùng khi |
|---|---|---|
| **`VisionGuard-GPU.zip`** | 3,08 GB | Máy khách **có card NVIDIA** — nhanh nhất |
| **`VisionGuard-Setup.exe`** | 377 MB | Máy khách **không có card rời**, hoặc muốn gọn |

Cả hai đều không cần cài Python và không cần mạng trên máy đích.

---

# Gói GPU — `VisionGuard-GPU.zip`

Chép zip sang máy khách, **giải nén vào một thư mục ghi được** (ví dụ `C:\VisionGuard` —
đừng đặt trong `Program Files`, xem lý do bên dưới), rồi bấm đúp **`Install.bat`** bên
trong.

`Install.bat` tạo lối tắt, đăng ký tự khởi động khi đăng nhập Windows, rồi chạy kiểm tra
hệ thống.

Gói này kèm **torch 2.11.0+cu128**. Máy khách cần:

- Card NVIDIA đời Turing (GTX 16xx / RTX 20xx) trở lên
- Driver NVIDIA đủ mới cho CUDA 12.8 — driver từ 2025 trở đi là an toàn

Đã chạy thử trên GTX 1650: log ghi `YOLO loaded: yolo11n.pt | cuda:0`.

Nếu máy không có card hợp lệ, phần mềm vẫn chạy nhưng tự rơi về CPU (chậm hơn, vẫn dùng
được vì bộ phát hiện đã giới hạn 12 fps).

Vì sao là zip chứ không phải một file exe như bản CPU: với CUDA bộ gói nặng khoảng 5 GB,
mà bộ cài one-file sẽ phải giải nén cả 5 GB ra thư mục tạm **mỗi lần chạy**. Zip trung
thực hơn và chỉ tốn một lần giải nén.

---

# Gói CPU — `VisionGuard-Setup.exe` (bộ cài một file)

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

### Build lại hai gói

```
build\venv\Scripts\python.exe      build\make_installer.py                       (bo cai CPU, ~7 phut)
build\venv-gpu\Scripts\python.exe  build\make_zip.py --venv venv-gpu --name GPU   (zip GPU, ~10 phut)
```

Kết quả nằm trong `release\`.

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

Ở kịch bản này không có rò rỉ, và vấn đề nằm ở **CPU** và ở **đĩa**.

> **Phép đo này chưa đủ.** Nó chỉ đúng chừng nào giao diện còn theo kịp camera. Khi giao
> diện chậm lại thì có rò rỉ thật, và rất nặng — xem mục *Hàng đợi ảnh phình vô hạn* ở
> cuối tài liệu. Bài đo trên không bắt được vì nó chưa bao giờ làm giao diện quá tải.

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

## Lỗi crash lúc thoát — đã tìm ra và sửa

Đóng cửa sổ trong lúc model YOLO còn đang nạp thì tiến trình **chết với mã
`0xC0000409`**, không traceback, không dòng nào trong log. Launcher coi mọi mã thoát khác
0 là crash và bật lại phần mềm — nên trên máy khách đây là **ứng dụng không tắt được**.

Log đã nói ra từ đầu, chỉ là phải đọc ba dòng cùng nhau:

```
21:28:23  Shutting down
21:28:26  InferenceWorker did not stop in time      ← hết hạn chờ 3 giây
21:28:35  YOLO loaded: yolo11n.pt | cpu             ← 12 giây sau mới xong
```

Hàm tắt chờ mỗi worker 3 giây. Nạp yolo11n trên CPU mất khoảng 12 giây và **không gì ngắt
được** — cờ dừng không, `requestInterruption` cũng không, vì luồng đang nằm sâu trong
torch. Hết hạn, nó ghi cảnh báo rồi bỏ đi, để lại một `QThread` còn sống; huỷ luồng đang
chạy thì Qt gọi `qFatal`, mà trên Windows `qFatal` là fast-fail không cứu được.

Giờ worker báo nó đang nạp model và hàm tắt chờ hết (trần 30 giây). Nếu một worker vẫn
không dừng, phần mềm **thoát ngay bằng `os._exit(0)`** thay vì huỷ luồng đang chạy —
không dùng `QThread.terminate()`, vì giết luồng đang giữ GIL làm tiến trình **treo**, mà
treo còn tệ hơn crash: launcher bật lại được tiến trình đã thoát, nhưng không thấy được
tiến trình bị kẹt. Lúc đó mọi thứ cần ghi đã ghi xong: clip đã đóng, CSDL đã đóng, config
đã lưu từ trước.

Cửa sổ cũng ẩn đi trước khi tắt worker, nên chờ nạp model là *cửa sổ biến mất, tiến trình
nán lại* chứ không phải cửa sổ đứng hình 10 giây.

Đo trước khi sửa: 3/5 lần crash khi máy rảnh, 7/7 khi máy đang tải nặng. Sau khi sửa: sạch.

## Mật khẩu camera trong log — đã bịt

Hàm che URL cắt ở dấu `@` **đầu tiên**. Hikvision bắt buộc mật khẩu có ký tự đặc biệt và
`@` là lựa chọn phổ biến, nên `rtsp://admin:Hik@2026!@192.168.1.64/...` bị che thành
`rtsp://admin:***@2026!@192.168.1.64/...` — **lọt `2026!` ra log**. Giờ cắt ở `@` cuối
cùng của phần authority, đúng như RFC 3986, và cả dự án dùng chung một hàm duy nhất.

Và như mọi khi: đây là thiết bị giám sát, không phải thiết bị an toàn đạt chuẩn.

## Hàng đợi ảnh phình vô hạn — lỗi nặng nhất, đã sửa

`CameraWorker` phát tín hiệu ảnh sang luồng giao diện cho **mỗi frame bắt được**. Đó là
kết nối *queued*, và kết nối queued **không có cơ chế chặn ngược**: giao diện chậm hơn
camera thì sự kiện chất đống trong hàng đợi, mỗi sự kiện ôm một ảnh đầy đủ. Không ai xoá
chúng — chúng chỉ chờ.

Đo với 3 camera đọc hết tốc lực:

| | Trước | Sau |
|---|---|---|
| Ảnh đẩy sang giao diện | 652/giây | 64/giây |
| Bộ nhớ sau 49 giây | **9.955 MB, vẫn tăng** | ~710 MB |
| Độ dốc sau khi ổn định (150 giây) | — | **−12 MB/phút** (nhiễu) |

Đây không chỉ là chuyện của bài kiểm thử. Ba camera RTSP thật ở 25 fps đẩy khoảng
**67 MB/giây** vào hàng đợi đó. Nó chỉ rỗng chừng nào giao diện còn theo kịp — một lần vẽ
lại chậm, một hộp thoại đang mở, một chương trình khác chiếm CPU, là nó bắt đầu dâng.
Khựng 30 giây là 2 GB. Với phần mềm chạy hàng tháng, đó là ngòi nổ chậm.

Giờ camera chỉ đưa ảnh mới **sau khi giao diện đã nhận xong ảnh cũ**, và không nhanh hơn
tốc độ vẽ của màn hình (`ui_fps_limit`). Hàng đợi bị chặn ở **1 ảnh mỗi camera**, bất kể
giao diện chậm đến đâu.

**Phát hiện người không bị ảnh hưởng.** Luồng suy diễn đọc từ `LatestFrameBuffer`, vốn chỉ
giữ ảnh mới nhất — bỏ qua một ảnh xem trước không bao giờ làm mất một lần phát hiện.

## Trạng thái kiểm thử

14 kịch bản, chạy 2 lượt, **toàn bộ sạch**: `ui_acceptance`, `multicam_e2e`, `multicam_ui`,
`multicam_ux`, `layout_stability`, `clip_recording`, `clip_ui`, `roi_persist`,
`widget_interaction`, `ai_camera_e2e`, `ai_ui_test`, `header_shot`, `window_shot`,
`deploy_check`.

---

# Giao diện PLC — chỉ 2 thiết bị

| Thiết bị | Ý nghĩa |
|---|---|
| **M100** | `1` khi có người trong vùng giám sát, `0` khi vùng trống |
| **M110** | Đảo trạng thái mỗi 500 ms khi hệ thống **đang chạy VÀ nhìn được** |

Không ghi gì khác. Trước đây có thêm M101 (vùng trống), M102 (camera OK), M103 (AI chạy),
M104 (lỗi), D100 (từ trạng thái) và M200+ (từng vùng) — đã bỏ.

## Nhịp tim mang luôn ý nghĩa sức khoẻ

Đây là điểm quan trọng nhất của thiết kế 2 bit. M110 **đứng lại** khi:

- Camera chết hoặc mất kết nối
- Model AI hỏng
- Người vận hành bấm STOP
- Phần mềm treo hoặc tắt

Lý do: nếu chỉ đọc M100 thì **camera chết trông y hệt vùng trống** — cả hai đều là `0`.
Máy chạy tiếp trong khi không ai nhìn cái vùng đó. Đó đúng là kiểu hỏng làm người ta bị
thương. Đóng băng nhịp tim để watchdog mà PLC vốn đã phải có bắt được luôn trường hợp
camera mù, không tốn thêm bit nào.

## Ladder cần viết

```
Nếu M110 KHÔNG đổi trạng thái trong 2 giây  →  coi như hệ thống mù  →  xử lý như có người
Nếu M100 = 1                                →  có người trong vùng  →  dừng máy
```

Chỉ đọc M100 mà bỏ qua watchdog M110 là **bỏ mất toàn bộ khả năng phát hiện camera hỏng**.

## Lấy lại các bit cũ nếu cần

Chúng không bị xoá khỏi code, chỉ để trống trong cấu hình. Vào tab **PLC**, điền tên thiết
bị vào ô tương ứng là nó ghi trở lại. Cờ `heartbeat.stop_on_fault` trong
`config/plc_config.json` đặt `false` nếu muốn nhịp tim chạy tự do như trước.

---

# Mở app là chạy — không cần bấm gì

Hai cài đặt, cả hai đã bật sẵn:

| Cài đặt | File | Ý nghĩa |
|---|---|---|
| `detector.auto_load_on_start` | `config/ai_config.json` | Nạp model ngay khi mở app, không đợi START |
| `app.autostart` | `config/app_config.json` | Tự bắt đầu giám sát, không cần ai bấm START |
| `app.autostart_timeout_s` | `config/app_config.json` | Hạn chót 25 giây — model hỏng thì vẫn phải khởi động |

Cờ dòng lệnh `--autostart` vẫn dùng được, giờ là cách ghi đè chứ không phải cách duy nhất.

## Vì sao bỏ mốc chờ 3 giây cũ

Trước đây tự start sau đúng 3 giây. Đó là phỏng đoán sai cả hai chiều: quá sớm khi máy
nguội (model mất 12 giây), quá muộn khi máy nóng (model xong sau 2 giây). Giờ hệ thống
**chờ model thật sự sẵn sàng rồi mới bắt đầu** — đo được 0,02 giây giữa hai mốc.

Vẫn có hạn chót, vì một model hỏng vĩnh viễn không được phép biến thành một hệ thống
không bao giờ khởi động: camera vẫn chạy và người vận hành vẫn cần thấy báo lỗi.

## Nạp model mất bao lâu, và vì sao không thể nhanh hơn

Đo trên máy rảnh, bản CPU:

| Giai đoạn | Thời gian |
|---|---|
| `import torch` | 1,62s |
| warm-up — lần suy diễn đầu tiên | 1,64s |
| đọc file `.pt` | 0,05s |
| suy diễn thật (mỗi khung sau đó) | 0,06s |
| **Tổng** | **3,63s** |

Hai mục đầu chiếm 90%. Đã thử thu ảnh warm-up từ 640×640 xuống 64×64: **vẫn tốn 1,62s** —
đó là chi phí khởi tạo nhân tính toán của torch, không liên quan kích thước ảnh.

`import torch` giờ chạy trên luồng nền **ngay từ dòng đầu của `main.py`**, song song với
việc dựng giao diện thay vì nối đuôi. Đo được: **5,7 → 5,1 giây** từ lúc mở app đến lúc
bắt đầu nhìn thấy.

Khoảng 5 giây là sàn thực tế của một ứng dụng YOLO trên PyTorch.

> **Trên máy có GPU sẽ CHẬM HƠN, không nhanh hơn.** Khởi tạo CUDA context tốn thêm vài
> giây khi nạp. Đổi lại mỗi khung hình suy diễn nhanh hơn nhiều khi đã chạy.
