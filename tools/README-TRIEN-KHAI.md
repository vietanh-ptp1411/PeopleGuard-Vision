# VisionGuard — cài đặt và chạy 24/7 trên máy khách

Có hai gói sẵn trong `release\`:

| Gói | Dung lượng | Dùng khi |
|---|---|---|
| **`VisionGuard-GPU.zip`** | 3,13 GB | Máy khách **có card NVIDIA** — nhanh nhất |
| **`VisionGuard-Setup.exe`** | 377 MB | Máy khách **không có card rời**, hoặc muốn gọn |

Cả hai đều không cần cài Python và không cần mạng trên máy đích.

---

# Gói GPU — `VisionGuard-GPU.zip`

Chép zip sang máy khách, **giải nén vào một thư mục ghi được** (ví dụ `C:\VisionGuard` —
đừng đặt trong `Program Files`, xem lý do bên dưới), rồi bấm đúp **`Install.bat`** bên
trong.

`Install.bat` tạo lối tắt, đăng ký tự khởi động khi đăng nhập Windows, rồi chạy kiểm tra
hệ thống.

Gói này kèm **torch 2.14.0+cu126**. Máy khách cần:

- Card NVIDIA đời Turing (GTX 16xx / RTX 20xx) trở lên
- Driver NVIDIA đủ mới cho CUDA 12.6 — driver từ 2025 trở đi là an toàn

Gói kèm sẵn **hai model** (`model1` = YOLO26s, `model2` = YOLO26m), không phải cài thêm gì.
Mặc định dùng `model1` ở imgsz 1280.
Chi tiết ở mục **Chọn model YOLO**.

> ⚠️ **Nếu máy khách bật Smart App Control** (Windows 11), phần mềm sẽ **không chạy được**:
> Windows chặn các DLL của PyTorch với lỗi `WinError 4551`. Kiểm tra trước ở
> **Windows Security → App & browser control → Smart App Control**. Tắt nó là **không thể
> bật lại** trừ khi cài lại Windows — nên phải hỏi ý khách trước khi tắt.

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

# Lần chạy đầu — phần mềm được giao ở trạng thái **chưa cấu hình**

Cả hai gói đều chở theo một `config` trắng: **1 camera, không IP, không vùng ROI, PLC ở
chế độ mô phỏng**. Nên ngay cuối `Install.bat` sẽ có dòng:

```
[FAIL]  Camera 1   no IP configured
KHONG THE CHAY: sua cac muc [FAIL] o tren roi thu lai.
```

**Đây là đúng, không phải lỗi cài đặt.**

Trước đây gói chở theo config của máy build — đường dẫn video trong `Downloads`, bốn vùng
ROI thừa, IP camera mẫu. Bài kiểm báo "SẴN SÀNG" trong khi phần mềm không nhìn thấy gì
cả. Một bài kiểm chỉ có ích khi nó **hỏng ở đây, trên bàn**, chứ không phải ở hiện trường
lúc dây đã đấu xong.

### Thứ tự cấu hình

| # | Việc | Ở đâu |
|---|---|---|
| 1 | Mở `VisionGuard.exe` — lối tắt Desktop, **không** phải Launcher | |
| 2 | Nhập IP, tài khoản, mật khẩu camera | tab **CAMERA** |
| 3 | Vẽ vùng cấm người vào | tab **ROI** |
| 4 | Nhập IP PLC, rồi **bỏ** dấu tích *Simulation* | tab **PLC** |
| 5 | Lưu lại, đóng phần mềm | |
| 6 | Chạy `VisionGuardLauncher.exe --check-only` đến khi hết `[FAIL]` | |

Bước 4 là bước duy nhất chạm vào phần cứng thật. **Để nguyên *Simulation* thì không một
bit nào được ghi ra PLC** — bài kiểm nhắc điều đó bằng một dòng `[WARN]` ở mỗi lần khởi
động, kể cả khi mọi mục khác đã đạt.

### Vì sao lối tắt Desktop trỏ vào `VisionGuard.exe` chứ không phải Launcher

Launcher **từ chối khởi động** khi bài kiểm còn `[FAIL]`: nó thử lại trong 3 phút rồi
dừng và ghi lý do vào `logs\launcher.log`. Đúng với một máy chạy 24/7 không người trông
— nhưng sẽ thành ngõ cụt nếu lần đầu khách bấm vào nó, vì chưa cấu hình thì không vào
được, mà không vào được thì không cấu hình được.

Vì vậy lối tắt Desktop đi thẳng vào `VisionGuard.exe`, luôn mở được kể cả khi chưa cấu
hình gì. Launcher chỉ nằm ở tác vụ tự khởi động và ở Start Menu.

Tác vụ tự khởi động đã đăng ký ngay lúc cài, nên cấu hình xong chỉ cần đăng xuất rồi đăng
nhập lại là máy tự chạy — không phải cài lại.

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

# Giao diện PLC — 3 thiết bị

| Thiết bị | Ý nghĩa |
|---|---|
| **M100** | `1` khi có người trong vùng **Alarm**, `0` khi các vùng Alarm trống |
| **M101** | `1` khi có người trong vùng **Warning**, `0` khi các vùng Warning trống |
| **M110** | Đảo trạng thái mỗi 500 ms khi hệ thống **đang chạy VÀ nhìn được** |

M100 và M101 độc lập: một người đứng đè lên cả hai loại vùng thì cả hai cùng `1`. Site chỉ
cần một mức thì vẽ toàn vùng Alarm, M101 sẽ nằm im ở `0` (hoặc xoá trống ô *Vùng WARNING*
trong tab PLC để không ghi nó nữa). Không ghi gì khác. Trước đây có thêm bit vùng trống,
M102 (camera OK), M103 (AI chạy), M104 (lỗi), D100 (từ trạng thái) và M200+ (từng vùng) — đã bỏ.

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
Nếu M100 = 1                                →  có người trong vùng Alarm    →  dừng máy
Nếu M101 = 1                                →  có người trong vùng Warning  →  giảm tốc / báo đèn còi
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

---

# Chọn model YOLO

Gói kèm **hai model** trong `models/`, chọn ở tab **AI Model**. Tên đã đổi thành
`model1` / `model2` để ngoài hiện trường không phải đọc tên kỹ thuật — `models/README.txt`
ghi rõ file nào là bản YOLO nào.

| File | Thực chất | Dung lượng | Vai trò |
|---|---|---|---|
| `model1.pt` | YOLO26 small — 10.0M tham số | 19 MB | **mặc định của gói này** |
| `model2.pt` | YOLO26 medium — 21.9M tham số | 42 MB | dự phòng cho máy card mạnh |

## Đo trên GTX 1650, khung hình thật 2688×1520

Toàn bộ số dưới đây đo bằng frame lấy trực tiếp từ camera Hikvision 4MP, FP32.

| Model | imgsz 640 | imgsz 960 | imgsz 1280 |
|---|---|---|---|
| `model1` | 21.0 ms | 25.7 ms | **42.5 ms** |
| `model2` | 32.0 ms | 56.7 ms | 93.6 ms |

Cấu hình mặc định của gói: **`model1` ở imgsz 1280**, chiếm 64 % ngân sách khi chạy
1 camera ở 15 fps.

## Vì sao imgsz quan trọng hơn cỡ model

Camera 4MP cho khung 2688 pixel ngang. Hạ xuống imgsz 640 là **vứt đi 3/4 số pixel**:
một người ở rìa xa vùng giám sát cao chừng 40 pixel sẽ còn **10 pixel** — không model nào
cứu được, kể cả bản `x`.

Nên thứ tự ưu tiên khi thấy bỏ sót người ở xa là **nâng imgsz trước, đổi model sau**.
`model1` ở 1280 nhìn gấp 4 lần số pixel so với `model2` ở 640, mà còn rẻ hơn 10 ms.

Ngược lại, nếu vùng giám sát **gần và nhỏ** (ví dụ 5×5 m, người luôn chiếm trên 150 pixel)
thì độ phân giải không còn là chỗ tắc — lúc đó `model2` ở imgsz 640 hợp hơn: rẻ hơn
10 ms mà có gấp đôi năng lực model để xử lý người bị che một phần, dáng cúi, ngồi, quỳ.

## ⚠️ Đừng bật FP16 trên card GTX 16xx

Ô **FP16 on CUDA** trong tab AI Model nghe như sẽ nhanh hơn. Trên GTX 1650 nó **chậm gấp 3**:

| | FP32 | FP16 |
|---|---|---|
| `model2` @ 640 | 32 ms | **108 ms** |
| `model1` @ 1280 | 42 ms | **140 ms** |

GTX 16xx không có tensor core nên FP16 rơi vào đường tính chậm. Trên card **RTX** (Ampere,
Ada) thì ngược lại — FP16 sẽ có lợi thật, nhưng **phải đo lại trên đúng máy đó**, đừng suy
từ bảng này.

## Điều model nào cũng không sửa được

Camera treo cao nhìn chéo xuống. Mọi model YOLO đều học từ bộ ảnh COCO, mà COCO chủ yếu là
**ảnh chụp ngang tầm mắt**. Người nhìn từ trên cao trông khác hẳn — chỉ thấy đầu và vai.

Ba thứ ảnh hưởng nhiều hơn việc đổi model:

- **Ngược sáng.** Người đứng trước cửa sổ thành bóng đen. Bật **WDR** trên web camera
  (Configuration → Image → Backlight Settings), không phải sửa ở phần mềm.
- **Góc lắp.** Nghiêng 30–40° là vùng tốt. Vượt 45–50° thì thành nhìn thẳng đỉnh đầu.
- **Người quá nhỏ trong khung.** Đặt xa quá hoặc góc quá rộng thì không còn pixel để nhận.

Cách trả lời dứt điểm: quay vài phút video từ **đúng vị trí camera đã lắp**, rồi chạy lại
phép đo trên đoạn đó.

---

# Tab Lưu trữ — ảnh, clip, lịch sử, nhật ký

Gom toàn bộ cấu hình lưu trữ vào một chỗ: **lưu ở đâu, chất lượng nào, giữ bao nhiêu ngày**.

| Nhóm | Điều chỉnh |
|---|---|
| **Nơi lưu** | Thư mục cho ảnh, clip, nhật ký, file SQLite. Hiện dung lượng ổ còn trống |
| **Ảnh sự kiện** | Bật/tắt, chất lượng JPEG, số ngày giữ |
| **Video clip** | Clip ngắn quanh lúc có người: giây trước/sau, cỡ hình, số ngày, trần GB |
| **Lịch sử & nhật ký** | Số ngày giữ sự kiện và log, trần số dòng |

**Ảnh chỉ lưu khi có người VÀO vùng**, một ảnh cho mỗi lần — không phải mỗi khung hình.
Ảnh toàn khung 2688×1520 ở chất lượng 90 nặng khoảng **580 KB**. Hạ chất lượng xuống 80
giảm được chừng 40 % mà vẫn đủ rõ để xét lại sự kiện.

> **Nếu thấy hàng chục ảnh trong vài phút**, đó gần như chắc chắn là **tín hiệu nhấp nháy**
> chứ không phải người ra vào thật. Xem mục `off_delay` bên dưới.

## `off_delay` — đừng để dưới 500 ms

Trong tab **AI Model** có `off delay`. Đặt quá ngắn (100 ms) thì chỉ cần YOLO trượt một hai
khung — chuyện thường khi người bị che nửa giây — là vùng bị tuyên bố trống rồi lại có
người ngay. Đo thực tế với `off_delay = 100 ms`:

```
13:36:04.941  PERSON_ENTERED
13:36:05.315  PERSON_LEFT      (+374 ms)
13:36:05.715  PERSON_ENTERED   (+400 ms)
13:36:06.155  PERSON_LEFT      (+440 ms)
```

Bốn lần vào/ra trong 1,2 giây. Mỗi lần là **một lệnh ghi thật xuống PLC** — máy vừa dừng đã
chạy, chạy rồi lại dừng. Với tín hiệu bảo vệ người thì không chấp nhận được.

**Để `off_delay` khoảng 500–1000 ms.** Người rời vùng thật thì chậm 1 giây không sao; đầu
ra PLC nhấp nháy thì nguy hiểm hơn nhiều.

# Camera mất kết nối — phần mềm tự vào lại

Camera rớt (rút cáp, mất điện camera, switch reset) thì phần mềm **tự thử lại mãi** theo
nhịp 1s → 2s → 5s cho tới khi vào được. Không cần bấm gì, không cần khởi động lại app.

Trước bản này, vòng thử lại **chỉ chạy cho camera đang stream rồi mới rớt**. Còn camera đã
chết sẵn lúc bấm START — hoặc lúc bấm Connect lại sau đó — thì vào trạng thái `ERROR` và
**đứng im vĩnh viễn**: đợi bao lâu, cắm lại cáp thế nào cũng không tự vào, chỉ khởi động lại
app mới được. Đã sửa: mọi lần không vào được camera đều đi vào vòng thử lại, miễn là người
dùng vẫn đang muốn kết nối. Bấm **Disconnect** thì mới dừng thử.

Kiểm chứng đủ 4 giai đoạn: bấm START lúc camera chết → tự vào khi camera lên → rút cáp lúc
đang stream → tự vào lại khi cắm lại. Không khởi động lại app ở bước nào.

**Lý do thất bại hiện ngay trên màn hình.** Ô CAMERA và thanh cảnh báo vàng ghi rõ vì sao,
không chỉ "Đang kết nối lại...". Log ghi từng lần thử kèm nguyên nhân:

```
WARNING | CAMERA | Camera reconnect attempt 3 failed: Cannot open stream rtsp://... 
```

---

# Camera công nghiệp Hikrobot (MVS)

Ngoài camera IP qua RTSP, phần mềm chạy được **camera công nghiệp Hikrobot** (GigE Vision /
USB3 Vision) qua MVS SDK — chọn `Hikrobot (MVS)` ở ô **Camera Type** trong tab Camera.

**Chỉ cần cài MVS của Hikrobot rồi mở lại phần mềm.** Không phải thêm `PYTHONPATH` gì cả:
phần mềm tự tìm thư mục `MvImport` theo thứ tự

1. biến môi trường `MVCAM_COMMON_RUNENV` (MVS tự đặt khi cài)
2. `MVCAM_SDK_PATH`
3. `C:\Program Files (x86)\MVS\Development\Samples\Python\MvImport`
4. thư mục `MvImport` đặt cạnh file exe — dùng khi máy khách không cài được MVS

Đường dẫn tìm được ghi vào log lúc khởi động (`Hikrobot MVS binding: ... (dll: ...)`) — chỗ
để xem khi máy có nhiều bản MVS.

> **Ghi chú kỹ thuật:** bản đóng gói **không tìm DLL theo PATH được**. MVS đặt
> `MvCameraControl.dll` ở `Common Files\MVS\Runtime\Win64_x64` và thêm vào PATH, nhưng
> bootloader của PyInstaller gọi `SetDefaultDllDirectories`, và lệnh đó **loại PATH khỏi thứ
> tự tìm DLL**. Chạy từ source thì được, đóng gói thì báo `Failed to load dynlib/dll
> 'MvCameraControl.dll'` — nghe như lỗi đóng gói, thực ra là PATH. Phần mềm nạp sẵn DLL này
> theo đường dẫn tuyệt đối trước khi gọi SDK, nên không phải làm gì thêm.

Đo trên MV-CE050-30GM (GigE, 5MP đơn sắc): **2592×1944 @ 14 fps**, ảnh đơn sắc được đổi sang
3 kênh trước khi vào YOLO nên nhận người bình thường.

> Camera GigE cắm trực tiếp vào PC sẽ nhận IP dạng `169.254.x.x` (không có DHCP) — vẫn chạy,
> không cần sửa gì. Đặt IP tĩnh cùng dải nếu muốn nó lên nhanh hơn.

Vùng ROI lưu ở **toạ độ tương đối 0–1**, nên đổi camera không làm mất vùng — nhưng khung
4:3 của camera công nghiệp khác 16:9 của camera IP, hình sẽ lệch tỉ lệ so với vùng đã vẽ.
Đổi camera thì nên vẽ lại vùng.

---

# Giao diện — vài điểm khác so với bản cũ

- **Không còn tab History.** Sổ sự kiện `events.db` vẫn ghi đầy đủ và ảnh vẫn lưu, chỉ là
  không còn bảng xem trong app.
- **Không còn tab Ghi hình** (ghi video 24/7 vào ổ cứng) và không còn cần ffmpeg. Muốn lưu
  video liên tục thì dùng đầu ghi NVR hoặc thẻ nhớ của camera — đó là việc của thiết bị ghi,
  không phải của phần mềm giám sát. Clip theo sự kiện ở tab **Lưu trữ** vẫn còn.
- **Không còn khung SYSTEM LOG** dưới màn hình. Nhật ký vẫn ghi vào `logs\<ngày>.log`,
  mở bằng Notepad khi cần.
- **Khung hình cố định**, không kéo được nữa — màn hình đặt ngoài xưởng hay bị tì tay làm
  lệch, kéo xong không ai biết cách trả về.
- **Nút thu gọn bảng điều khiển** (dải mảnh bên trái các tab, hoặc phím **F9**): gập panel
  sang phải để xem hình lớn. Trên màn 1900px, khung hình tăng từ 1385 lên **1855 px**.
- **START / STOP / PLC SIM và Connect / Disconnect nằm ở đáy tab Overview**, không còn
  thanh nút riêng. Đổi lại khung hình chạy thẳng tới mép dưới cửa sổ (cao thêm 16%).
  Hệ quả: gập panel bằng F9, hoặc đang mở tab khác, thì không thấy nút — **F5 chạy,
  F6 dừng** vẫn dùng được mọi lúc.
- **Không còn thanh trạng thái ở đáy cửa sổ.** Các câu xác nhận kiểu "đã lưu cấu hình"
  giờ chỉ ghi vào log ở mức DEBUG (`main.py --log-level DEBUG` để xem). Lỗi thật thì
  không đi đường đó: ô trạng thái đổi đỏ, thanh cảnh báo hiện lên, và log ghi lại.

---

# Số camera tối đa

**6 camera** cho mỗi bản chạy VisionGuard.

Giao diện **không phải** giới hạn: lưới dựng được 9 ô vẫn vừa màn hình 1536×816. Giới hạn
thật là **card đồ hoạ**. Đo trên GTX 1650, imgsz 640:

| Model | ms/khung | GPU mỗi camera @ 12 fps | Tối đa |
|---|---|---|---|
| `yolo11n` / `yolo11s` | 14 | 17 % | **5 camera** |
| `yolo11m` | 28 | 34 % | 2 camera |

Đặt giới hạn 6 chứ không phải 5 vì đây là **đánh đổi, không phải bức tường**: 6 camera chạy
được nếu hạ `max_fps` từ 12 xuống 10 (6 × 10 × 14 ms = 84 % card). Card mạnh hơn thì
nhiều hơn nữa.

Cần trên 6 thì sửa `MAX_CAMERAS` trong `visionguard/config/schemas.py` — nhưng hãy tính
tải card trước, vì phần mềm sẽ không tự ngăn bạn chọn quá sức máy.

# Ba loại ô nhập trong giao diện

| Loại | Nhận biết | Dùng để |
|---|---|---|
| **Combo** | Nền xám, có tam giác bên phải | Chọn trong danh sách có sẵn |
| **Spin box** | Nền trắng, mũi tên tăng/giảm | Nhập số |
| **Text box** | Trắng trơn, bên phải trống | Gõ chữ tự do |

Trước đây cả ba trông giống hệt nhau: QSS đặt `QComboBox::drop-down` không viền và **không
vẽ mũi tên nào**, mà khi một widget đã được style bằng stylesheet thì Qt bỏ mũi tên mặc
định của hệ thống.

Mũi tên là **file ảnh** trong `visionguard/app/assets/`, không phải CSS. Mẹo vẽ tam giác
bằng `width: 0; height: 0` cộng border là thành ngữ của trình duyệt — Qt vẽ ra một ô vuông
đặc. Chỉ phát hiện được bằng cách chụp ảnh giao diện rồi nhìn.
