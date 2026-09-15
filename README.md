# VisionGuard

Ứng dụng desktop công nghiệp phát hiện người trong vùng giám sát và báo trạng thái sang **PLC Mitsubishi
(MC Protocol 3E)**. Hỗ trợ **hai chế độ phát hiện**, đổi qua lại ngay trong app:

| Chế độ | Ai phát hiện người | Luồng |
|---|---|---|
| **AI Camera** (mặc định) | Camera AI tự phát hiện, tự có vùng giám sát | RTSP hiển thị hình + kênh sự kiện (ISAPI/HTTP) → VisionGuard → PLC |
| **PC AI / YOLO** | Phần mềm chạy YOLO trên máy tính | Camera bất kỳ → YOLO → ROI polygon → debounce → PLC |

Ở chế độ AI Camera phần mềm **không chạy YOLO**: camera lo phần nhận diện (Human Detection, Intrusion,
Region Entrance/Exit), PC chỉ hiển thị hình, diễn giải sự kiện thành trạng thái vùng và điều khiển PLC.

> ⚠️ **LƯU Ý AN TOÀN** – Đây là hệ thống *machine vision / monitoring*, **không phải** thiết bị bảo vệ đạt chuẩn an toàn
> (safety-rated). Nếu dùng để bảo vệ người khỏi máy/robot nguy hiểm, hệ thống thực tế bắt buộc phải có safety PLC,
> safety sensor / light curtain / laser scanner đạt tiêu chuẩn (ISO 13849, IEC 62061, ISO 13855…). Lớp AI chỉ là lớp
> phát hiện / giám sát bổ trợ trừ khi toàn hệ thống được chứng nhận.

---

## 1. Tổng quan kiến trúc

### Chế độ AI Camera (mặc định)

```
                         AI CAMERA (Hikvision / Dahua / generic)
                                    │
              ┌─────────────────────┴──────────────────────┐
              │ RTSP video                                 │ AI event (ISAPI / HTTP)
              ▼                                            ▼
   RtspVideoReceiver  (CameraWorker thread)      CameraEventProvider (CameraEventWorker thread)
              │ latest frame                                │ CameraEvent da chuan hoa
              ▼                                            ▼
            UI hien thi                        CameraEventStateMachine
                                                 (human only, dem nguoi, debounce)
                                                           │
                                                           ▼
                                               SystemController  →  PlcManager
                                                                        │ MC Protocol 3E
                                                                        ▼
                                                              Mitsubishi PLC (M100/M101/...)
```

Ba kênh truyền thông **độc lập**: video, event và PLC tự kết nối lại riêng, UI hiển thị đúng trạng thái
từng kênh. Mất kênh event → `FAULT`, **không bao giờ tự báo AREA CLEAR**.

### Chế độ PC AI / YOLO (giữ nguyên từ bản trước)

```
 Camera Worker (QThread)        Inference Worker (QThread)              PLC Worker (QThread)
 ┌──────────────────────┐       ┌────────────────────────────────┐      ┌──────────────────────────┐
 │ USB / Video / RTSP   │ Frame │ YOLO11n (person)               │      │ PlcManager               │
 │ Basler / Hikrobot /  ├──────►│  → RoiProcessor (foot/center/  │      │  • map state → devices   │
 │ GenICam adapter      │ latest│    intersection, exclusion)    │──────►  • write ONLY on change  │
 │ auto-reconnect       │ frame │  → OccupancyTracker            │Output│  • heartbeat toggle      │
 └──────────┬───────────┘ buffer│    (debounce / state machine)  │State │  • auto-reconnect+resync │
            │                   └──────────────┬─────────────────┘      └───────────┬──────────────┘
            │  signals                         │ PipelineResult                     │ MC Protocol 3E (TCP)
            ▼                                  ▼                                    ▼
 ┌─────────────────────────────────────────────────────────────┐        ┌────────────────────────┐
 │ SystemController (UI thread) – derive RUNNING/FAULT/STOPPED │        │ Mitsubishi FX5U / iQ-R │
 │ events (SQLite), snapshots, config                          │        │ Q / L  hoặc  Simulated │
 └────────────────────────────┬────────────────────────────────┘        └────────────────────────┘
                              ▼
                 PySide6 MainWindow (VideoView + ROI editor, Status, Config tabs, Log)
```

Nguyên tắc:

* **UI không gọi trực tiếp YOLO / socket PLC** – mọi thứ qua worker thread và Qt signal/slot.
* **Frame buffer 1 slot**: AI luôn xử lý frame mới nhất, frame cũ bị drop → không bị delay tích lũy.
* **Chỉ ghi PLC khi trạng thái đổi** (trừ heartbeat). PLC mất kết nối → tự nối lại và ghi lại toàn bộ trạng thái.
* **Fail-safe**: camera/AI lỗi → `FAULT`, **không bao giờ tự báo AREA CLEAR**.
* Camera layer có abstraction (`BaseCamera`) để sau này dùng camera công nghiệp; PLC layer có `BasePLC` để thêm
  Modbus TCP / S7 / OPC UA / EtherNet/IP.

## 2. Cấu trúc thư mục

```
VisionGuard/
├─ main.py                         # entry point
├─ requirements.txt                # core: PySide6, opencv-python, numpy, ultralytics
├─ requirements-industrial.txt     # optional: pypylon, harvesters, lap
├─ requirements-dev.txt            # pytest
├─ visionguard/
│  ├─ app/
│  │  ├─ main_window.py            # layout + wiring
│  │  ├─ theme.py                  # light industrial palette + QSS
│  │  ├─ controllers/system_controller.py
│  │  └─ widgets/                  # video_view (ROI editor), workflow_bar (5 bước), status_panel, camera/ai/plc config, roi_panel, io_test, events, log
│  ├─ camera/
│  │  ├─ base_camera.py            # BaseCamera, Frame, CameraInfo
│  │  ├─ camera_manager.py         # factory video theo detection mode + scan + SDK availability
│  │  ├─ usb_camera.py  video_camera.py  rtsp_camera.py
│  │  ├─ video/rtsp_receiver.py    # RtspVideoReceiver (video cua AI Camera mode)
│  │  ├─ events/                   # AI CAMERA MODE
│  │  │  ├─ camera_event.py        # CameraEvent + CameraEventType + TargetType
│  │  │  ├─ base_event_provider.py # interface connect/start_listening/poll
│  │  │  ├─ event_manager.py       # factory theo brand / provider
│  │  │  ├─ hikvision/  isapi_client.py  event_parser.py  event_provider.py
│  │  │  ├─ dahua/      dahua_event_provider.py
│  │  │  ├─ mock/       mock_event_provider.py
│  │  │  └─ generic_http_event_provider.py
│  │  └─ industrial/ industrial_base.py  basler_camera.py  hikrobot_camera.py  genicam_camera.py
│  ├─ vision/  detection.py  detector.py  yolo_detector.py
│  ├─ roi/     geometry.py  roi_model.py  roi_manager.py  roi_processor.py
│  ├─ logic/   debounce.py  occupancy_state_machine.py  pipeline.py
│  │            camera_event_state_machine.py   # AI camera: event -> trang thai vung
│  ├─ plc/     base_plc.py  device_address.py  simulated_plc.py  plc_manager.py
│  │  └─ mitsubishi/ mc_protocol.py (3E Binary/ASCII frames)  mc_driver.py (TCP driver)
│  ├─ workers/ frame_buffer.py  camera_worker.py  inference_worker.py  plc_worker.py
│  │            camera_event_worker.py          # thread kenh su kien AI camera
│  ├─ config/  schemas.py (dataclasses)  config_manager.py (JSON)
│  │            region_mapping.py               # region id cua camera -> ten + device PLC
│  ├─ storage/ event_repository.py (SQLite)  snapshot_saver.py
│  └─ utils/   logger.py  performance.py  qt_image.py
├─ tools/mc_plc_simulator.py       # PLC Mitsubishi giả lập qua TCP (để test driver thật)
├─ tests/                          # pytest
├─ config/  models/  logs/  events/  resources/
```

## 3. Cài đặt

Yêu cầu **Python 3.11+** (đã kiểm thử với 3.12 trên Windows 11).

```bat
cd "D:\VisionGuard"
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

GPU NVIDIA (khuyến nghị): cài torch bản CUDA **trước** ultralytics, ví dụ

```bat
pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
pip install -r requirements.txt
```

Tuỳ chọn (camera công nghiệp, tracking):

```bat
pip install -r requirements-industrial.txt
```

### Model YOLO pretrained

Không cần train. Ứng dụng tìm model theo thứ tự: đường dẫn trong `config/ai_config.json` → `models/<tên file>` →
bất kỳ `models/*.pt` → **tự tải** `yolo11n.pt` (nếu `auto_download = true` và có internet).

Tải thủ công (khi máy offline):

```bat
mkdir models
curl -L -o models/yolo11n.pt https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo11n.pt
```

Có thể dùng `yolo11s.pt` để chính xác hơn (chậm hơn). Chỉ class `person` (COCO id 0) được inference.

## 4. Chạy

```bat
python main.py
```

hoặc `run.bat`. Log ghi vào `logs/YYYY-MM-DD.log`, event vào `events/events.db`, ảnh chụp vào `events/YYYY-MM-DD/`.

### Giao diện

Giao diện sáng (light industrial), bố cục cố định:

```
 toolbar    START SYSTEM | STOP SYSTEM | TEST VIDEO | PLC SIMULATION | AREA BANNER | LED x4 | dong ho
 workflow   (1) Camera  >  (2) AI Model  >  (3) ROI Zones  >  (4) PLC  >  (5) Run
 trai       hinh camera + ROI editor          |  phai   tab Status / 1 Camera / 2 AI / 3 ROI / 4 PLC / I/O / Events
 quick bar  CAMERA: Connect Start Stop  |  ROI: +ROI  +Exclusion  Finish  Edit  Save ROI
 duoi       SYSTEM LOG (auto scroll)           |  status bar: thong bao + canh bao an toan
```

**Thanh 5 bước** ngay dưới toolbar là phần quan trọng nhất khi thao tác: mỗi bước hiện trạng thái
(`xám` = chưa làm, `xanh dương` = đang làm, `xanh lá ✓` = xong, `vàng/đỏ` = thiếu hoặc lỗi) kèm mô tả
ngắn, ví dụ *Camera – Streaming*, *ROI Zones – 1 zone(s) + 1 exclusion*, *Run – Press START SYSTEM (F5)*.
Bấm vào một bước sẽ mở đúng tab tương ứng. Khi cả 5 bước xanh là hệ thống đang giám sát.

Trạng thái khu vực hiển thị đồng thời ở 3 chỗ để đọc được từ xa: banner trên toolbar, banner trên hình
camera và ô lớn trong tab Status — `AREA CLEAR` (xanh lá) / `PERSON DETECTED` (đỏ) / `FAULT` (vàng) /
`STARTING…` (xanh dương) / `STOPPED` (xám).

Toàn bộ màu nằm trong `visionguard/app/theme.py` (token + QSS). Đổi bảng màu ở một chỗ là cả
ứng dụng đổi theo; màu vẽ lên hình camera nằm riêng trong `VisualizationConfig` của `app_config.json`.

### Chạy thử nhanh bằng video - nút TEST VIDEO

Muốn xem hệ thống chạy ngay mà chưa có camera hay PLC: bấm **▶ TEST VIDEO** trên toolbar (phím tắt **F9**),
chọn 1 file video có người đi lại. Một cú bấm sẽ tự làm hết:

1. chuyển camera sang **Video File**, bật Loop và phát đúng FPS của file;
2. nếu **chưa có vùng giám sát nào**, tự tạo vùng mặc định tên *Demo Zone* (khung 8%–92% khung hình) và lưu lại;
3. nếu **chưa kết nối PLC nào**, tự bật **PLC Simulation** (PLC thật đang kết nối thì giữ nguyên);
4. nạp model YOLO nếu chưa nạp, mở camera, **START SYSTEM** và chuyển sang tab *Status*.

Sau vài giây cả 5 bước trên thanh workflow chuyển xanh, bounding box hiện trên video, `M100 -> ON` chạy trong
System Log và bảng PLC MEMORY. Bấm lại nút để đổi sang video khác (hệ thống tự dừng rồi khởi động lại).
Vùng *Demo Zone* có thể sửa hoặc xoá trong tab *3 ROI* như mọi ROI khác.

### Quy trình demo (MVP)

Làm theo đúng thứ tự 5 bước trên thanh workflow:

1. **Camera** – tab *1 Camera* → chọn *Camera Type* (USB / Video File / RTSP …) → **Apply & Save** →
   **Connect** → **Start**. Nhanh hơn: dùng nút **Connect / Start** ngay dưới khung hình.
2. **AI Model** – tab *2 AI* → **Load Model** (mặc định tự nạp khi mở app).
3. **ROI Zones** – nút **+ ROI** dưới khung hình (hoặc tab *3 ROI*) → click từng điểm trên hình →
   double-click / Enter để đóng polygon. **+ Exclusion** cho vùng loại trừ. Nhấn **Save ROI** để lưu.
4. **PLC** – tab *4 PLC*: giữ **PLC SIMULATION: ON** để demo, hoặc tắt simulation, nhập IP PLC thật rồi
   **Connect** / **Test Connection**.
5. **Run** – **START SYSTEM** (F5) trên toolbar. Dừng bằng **STOP SYSTEM** (F6).

Phím tắt: **F5** start, **F6** stop, **F9** test bằng video, **F11** fullscreen.

Cửa sổ **mở toàn màn hình (maximized)** sẵn. Nhấn **F11** để chuyển sang fullscreen không viền (hợp cho màn hình HMI đặt tại máy), nhấn F11 lần nữa để quay lại. Kích thước tối thiểu 1195x629 nên chạy được trên mọi màn hình từ 1280x720 trở lên.

## 4b. Chế độ AI Camera

### Cấu hình camera

Tab **1 Camera** → *Detection source* = **AI Camera** → chọn *Camera brand*. Trang **AI Camera** cần:

| Trường | Ví dụ | Ghi chú |
|---|---|---|
| IP Address | `192.168.1.64` | IP camera |
| HTTP Port | `80` | cổng ISAPI/HTTP |
| RTSP Port | `554` | cổng video |
| Username / Password | `admin` / … | tài khoản có quyền remote |
| Stream channel | `101` | Hikvision: 101 main, 102 sub |
| RTSP URL | (trống) | điền để ghi đè URL tự sinh |
| Event provider | `Hikvision ISAPI alertStream` | hoặc Dahua / Generic / **Simulated** |
| Event API path | (trống) | mặc định `/ISAPI/Event/notification/alertStream` |

Ba nút kiểm tra: **Test Camera** (đọc deviceInfo, kiểm tra IP + mật khẩu), **Test RTSP** (mở thử luồng hình),
**Test Event** (mở kênh sự kiện vài giây và báo lại camera gửi gì).

### Bật AI trên camera Hikvision

1. Web camera → *Configuration → Event → Smart Event* → bật **Intrusion Detection** (hoặc Region Entrance /
   Region Exiting / Line Crossing), vẽ vùng, đặt **Region ID**.
2. Trong *Target Detection* chọn **Human** (bỏ Vehicle) nếu camera hỗ trợ AcuSense.
3. Tab **Linkage Method** → tick **Notify Surveillance Center** (bắt buộc, không có tick này camera sẽ không
   đẩy event ra `alertStream`).
4. Bấm **Test Event** trong VisionGuard rồi đi vào vùng để xác nhận.

### Event được hiểu như thế nào

| Camera gửi | VisionGuard hiểu | Tác động |
|---|---|---|
| `fielddetection` active / inactive | `INTRUSION_START` / `INTRUSION_END` | giữ vùng OCCUPIED tới khi có inactive |
| `regionEntrance` | `REGION_ENTER` | +1 người trong vùng |
| `regionExiting` | `REGION_EXIT` | −1 người, về 0 mới CLEAR |
| `linedetection` | `LINE_CROSS` | xung, giữ OCCUPIED trong *Line cross hold* |
| `humandetection` / AcuSense | `PERSON_DETECTED` / `PERSON_CLEARED` | như intrusion |
| `videoloss` active | `CAMERA_ERROR` | cảnh báo kênh hình |
| `VMD` (motion thường) | bỏ qua | motion không phân biệt người |

**Chỉ mục tiêu HUMAN mới kích PLC.** Event có `targetType` là vehicle/animal/other bị bỏ (vẫn hiện trong
Event Monitor để kiểm chứng). Nếu camera không báo target type thì mặc định vẫn nhận; bật *Only accept events
with target = human* để siết chặt.

Hai tham số quan trọng trong **EVENT LOGIC**:

* **Clear timeout** (mặc định 5 s) – có camera không bao giờ gửi `inactive`. Nếu quá thời gian này không có
  alarm mới thì nhả trạng thái presence. Không áp dụng cho bộ đếm người.
* **Bộ đếm người** – thiếu một `REGION_EXIT` sẽ giữ vùng **OCCUPIED**, không bao giờ tự CLEAR sai.

### Map vùng của camera sang PLC

Tab **2 AI Event → Regions**. Region id mới xuất hiện tự động khi camera gửi event lần đầu.

```json
{ "regions": [
    { "camera_region_id": "1", "name": "Robot Zone",   "plc_device": "M200", "enabled": true },
    { "camera_region_id": "2", "name": "Loading Zone", "plc_device": "M201", "enabled": true } ] }
```

`M100` / `M101` / `D100` vẫn là trạng thái tổng của cả khu vực, `M200`/`M201` là bit riêng từng vùng.

### Event Monitor và RAW EVENT

Tab **2 AI Event** có 3 trang: **Event Monitor** (bảng sự kiện realtime kèm region, target, thời gian),
**Regions** (map vùng), **RAW EVENT** (payload thô camera gửi). RAW EVENT rất quan trọng khi gặp model camera
lạ vì mỗi hãng đặt tên event khác nhau. Mật khẩu không bao giờ được ghi ra log hay RAW EVENT.

### Demo khi chưa có camera AI

Đổi *Event provider* thành **Simulated events**, sang tab **2 AI Event** và bấm **Person Enter / Person Exit /
Intrusion ON / OFF / Vehicle / Disconnect**. Toàn bộ chuỗi state machine → debounce → PLC chạy thật, chỉ có
nguồn event là giả. Nút **TEST VIDEO** trong chế độ này cũng tự chuyển sang Simulated events và phát video
file làm hình nền.

## 5. Camera

### 5.1 USB Webcam
Device index (0,1,2…), độ phân giải, FPS, backend (`auto` = DirectShow trên Windows, có thể chọn `msmf`). Nút **Scan** liệt kê index khả dụng.

### 5.2 Video File
`.mp4 .avi .mov .mkv …` – Browse, **Loop**, **Real-time** (phát theo FPS file), Play/Pause, Restart, seek. Đây là cách demo
không cần camera thật (TEST 10).

### 5.3 RTSP / IP camera
Điền IP / Port / Username / Password / Path, hoặc dán trực tiếp **RTSP URL** (ưu tiên nếu không rỗng). Preset đường dẫn
theo hãng (chỉ là template, không hard-code):

| Hãng | Path mặc định |
|---|---|
| Hikvision | `/Streaming/Channels/101` (sub-stream `102`) |
| Dahua | `/cam/realmonitor?channel=1&subtype=0` |
| UNV | `/media/video1` |
| Ezviz (bật RTSP trong app) | `/h264/ch1/main/av_stream` |
| Axis | `/axis-media/media.amp` |
| Generic ONVIF | `/onvif1`, `/stream1` |

Transport `tcp` (khuyến nghị) / `udp`, timeout, **Test Connection**. Mất stream → tự nối lại theo `1s, 2s, 5s…`.
Mẹo: dùng **sub-stream** (720p) để giảm độ trễ và tải CPU.

### 5.4 Camera công nghiệp (GigE Vision / USB3 Vision / GenICam)

| Adapter | SDK | Cài đặt |
|---|---|---|
| **Basler** | pypylon (Pylon Runtime) | Cài [pylon Software Suite](https://www.baslerweb.com/) rồi `pip install pypylon`. Scan → chọn serial. Cấu hình Exposure (µs), Gain, Frame Rate, Trigger. |
| **Hikrobot** | MVS SDK (Python) | Cài MVS, thêm `<MVS>\Development\Samples\Python\MvImport` vào `PYTHONPATH` (hoặc copy `MvCameraControl_class.py` + `.dll` path). Nếu thiếu SDK, UI hiện *SDK not installed*, không crash. |
| **Generic GenICam** | harvesters + GenTL `.cti` | `pip install harvesters`; chọn file `.cti` của hãng (Basler `ProducerGEV.cti`, Daheng, FLIR/Spinnaker, Allied Vision Vimba, IDS, Baumer, Lucid Arena, JAI…). |

Tất cả adapter trả về **numpy BGR** thống nhất (`to_bgr()` xử lý Mono8 / Bayer / RGB). Adapter chưa có SDK trên máy
được viết theo API chính thức nhưng chưa thể kiểm thử với thiết bị thật – hãy test trước khi triển khai.

## 6. ROI

* **Polygon bất kỳ** – click trái thêm điểm P1…Pn, double-click / Enter / *Finish polygon* đóng Pn→P1, right-click undo,
  Esc cancel.
* **Edit ROI**: click chọn, kéo đỉnh, `Shift+click` lên cạnh để thêm đỉnh, right-click đỉnh để xoá, kéo bên trong để
  dời cả vùng, `Delete` xoá ROI.
* **INCLUDE** (vàng, đỏ khi occupied) và **EXCLUDE** (xám, gạch chéo). Người trong exclusion zone → `IGNORED – EXCLUSION ZONE`,
  không kích PLC. Thứ tự: *Exclusion → Include → Outside*.
* Mỗi ROI: `id, name, type, enabled, points, color, plc_device` (tab ROI → *Selected ROI* → Apply).
* Toạ độ **normalized (x/w, y/h)** lưu trong `config/roi_config.json` → đổi resolution/resize cửa sổ vẫn đúng.

```json
{
  "rois": [
    {"id": "ROI_001", "name": "Robot Zone", "type": "include", "enabled": true,
     "points": [[0.20, 0.30], [0.80, 0.30], [0.85, 0.85], [0.15, 0.85]], "plc_device": "M200"},
    {"id": "EX_001", "name": "Operator Walkway", "type": "exclude", "enabled": true,
     "points": [[0.10, 0.10], [0.30, 0.10], [0.30, 0.90], [0.10, 0.90]]}
  ]
}
```

### Logic xác định người trong ROI (tab AI)

| Mode | Cách tính |
|---|---|
| **Foot Point** (mặc định) | điểm chân `((x1+x2)/2, y2)` nằm trong polygon |
| Center Point | tâm bbox nằm trong polygon |
| Intersection Percentage | `area(bbox ∩ polygon) / area(bbox) ≥ threshold` (mặc định 30 %) |

### Debounce / state machine

`CLEAR → PENDING_OCCUPIED → OCCUPIED → PENDING_CLEAR → CLEAR`

* **ON delay** 200 ms **và** ≥ `min_detection_frames` (3) frame liên tục → OCCUPIED.
* **OFF delay** 1000 ms không có người → CLEAR. YOLO mất 1–2 frame → giữ OCCUPIED (TEST 3).
* Mỗi include-ROI có state machine riêng + một state machine tổng cho toàn khu vực.

## 7. PLC Mitsubishi – MC Protocol (SLMP) 3E frame, TCP

Driver tự viết (`plc/mitsubishi/`), không phụ thuộc thư viện ngoài, hỗ trợ **Binary** (khuyến nghị) và **ASCII**,
lệnh Batch Read/Write (0401/1401, bit & word) và Random Write bits (1402). Device: `M D X Y B W L F V SM SD R ZR TN CN …`
(X/Y hex; với iQ-F/FX5 X/Y hiểu là octal theo quy ước Mitsubishi).

API (`BasePLC`): `connect() disconnect() is_connected() read_bit() write_bit() read_bits() write_bits() read_word() write_word() read_words() write_words()`.

### 7.1 Cấu hình trong app (tab PLC)

| Tham số | Mặc định | Ghi chú |
|---|---|---|
| IP / Port | 192.168.1.10 / 5000 | port bạn khai trong GX Works |
| Frame / Code | 3E / Binary | phải khớp *Communication Data Code* trong PLC (sai → end code `C050`) |
| Network No. / PC No. | 0 / 255 (0xFF) | giá trị chuẩn cho kết nối trực tiếp CPU |
| Dest. Module I/O / Station | 0x03FF / 0 | CPU của trạm cục bộ |
| Timeout / Retry | 2 s / 2 | mất PLC được báo sau ≈ timeout × (retry+1) |
| Auto reconnect | on | 1 s, 2 s, 5 s… rồi ghi lại toàn bộ trạng thái (resync) |

### 7.2 Device mapping mặc định

| Device | Ý nghĩa |
|---|---|
| `M100` | PERSON_PRESENT / AREA_OCCUPIED |
| `M101` | AREA_CLEAR (Mode B) – **không bao giờ** ON cùng lúc với M100 |
| `M102` | Camera connected |
| `M103` | AI running (system RUNNING và AI có kết quả) |
| `M104` | System FAULT |
| `M110` | Heartbeat – PC toggle 0/1 mỗi 500 ms (chỉnh được, bật/tắt) |
| `D100` | Status word: `0` CLEAR, `1` OCCUPIED, `2` CAMERA ERROR, `3` PLC ERROR, `4` AI ERROR, `5` NOT RUNNING |
| ROI `plc_device` | mỗi include ROI có thể ghi bit riêng (vd `M200`, `M201`) |

Signal mode **A** (1 bit M100) hoặc **B** (M100 + M101). Fail-safe: khi FAULT bit PERSON giữ giá trị cuối (mặc định)
hoặc ép ON/OFF; bit CLEAR luôn OFF khi FAULT; `D100` = mã lỗi. Khi STOP SYSTEM: `M103 = 0`, `M101 = 0`, `D100 = 5`,
PERSON giữ nguyên. PLC nên coi **heartbeat đứng > 2 s hoặc M103 = 0 hoặc M104 = 1** là "PC không giám sát" → về trạng thái an toàn.

Ví dụ ladder (FX5U):
```
|--[M110 rising/falling edge → reset timer T0 (K30 = 3 s)]--
|--[ T0 ]----------------------------------( M300 PC_LOST )--
|--[ M100 ]--+--[ M104 ]--+--[ M300 ]--+---( Y0 STOP_ROBOT )--   // OR các điều kiện dừng
```

### 7.3 Thiết lập PLC trong GX Works3 (iQ-F FX5U)

1. *Navigation → Parameter → FX5UCPU → Module Parameter → Ethernet Port*.
2. **Basic Settings → Own Node Settings**: IP (vd `192.168.1.10`), Subnet mask, Default gateway.
3. **External Device Configuration** → kéo **SLMP Connection Module** vào bảng: *Protocol = TCP*, *Port No. = 5000*
   (1025–65534, tránh 5551/5552), Communication Data Code = **Binary** (hoặc ASCII nếu chọn ASCII trong app).
4. Có thể thêm nhiều SLMP Connection để vừa PC vừa GOT/SCADA nối cùng lúc.
5. *Apply* → *Online → Write to PLC* (Parameter) → **Reset / tắt mở nguồn PLC**.
6. Nếu ghi bị từ chối khi RUN: *CPU Parameter → Operation Related Setting → Remote Reset / Permit write during RUN*.

### 7.4 GX Works2 (Q / L series, cổng Ethernet built-in)

1. *PLC Parameter → Built-in Ethernet Port Setting*: IP, Communication Data Code **Binary**, tick **Enable online change (FTP, MC Protocol)** (bắt buộc để ghi khi RUN).
2. **Open Setting**: Protocol TCP, Open System **MC Protocol**, Host Station Port No. (hex) `1388` = 5000.
3. Write to PLC → Reset.

iQ-R (GX Works3): *Module Parameter → Ethernet Port → Own Node Settings / External Device Configuration → SLMP Connection Module* tương tự FX5.

### 7.5 Test PLC không cần AI

* Tab **I/O Test**: Write ON/OFF/Value, Read bất kỳ device; nút nhanh cho các device đã map (`M100 ON/OFF`, `Read D100`…).
* Tab **PLC → Test Connection** đọc heartbeat device.
* **PLC giả lập qua TCP** (test driver MC thật khi chưa có PLC):

  ```bat
  python tools\mc_plc_simulator.py --port 5000        (thêm --ascii cho ASCII frame)
  ```
  Tắt *Simulate PLC*, IP `127.0.0.1`, port `5000` → Connect. Mọi lệnh ghi được in ra console.

## 8. Simulation Mode (bắt buộc cho demo)

Nút **Simulate PLC** (toolbar hoặc tab PLC): không mở socket, mọi write vào bộ nhớ ảo, bảng **PLC MEMORY** hiển thị
`M100 = ON/OFF`, `M101`, `M110` heartbeat, `D100`… realtime. Toàn bộ software (camera/video → AI → ROI → debounce → PLC)
chạy được chỉ với webcam hoặc file video.

## 9. Fail-safe & trạng thái hệ thống

| Tình huống | Area | System | PLC |
|---|---|---|---|
| Bình thường, không người | CLEAR | RUNNING | M100=0 M101=1 D100=0 |
| Có người trong include ROI (sau ON delay) | OCCUPIED | RUNNING | M100=1 M101=0 D100=1 (+ROI device) |
| Người chỉ ở exclusion zone / ngoài ROI | CLEAR | RUNNING | M100=0 |
| Camera mất / video hết | **FAULT** | FAULT | M104=1 M102=0 M101=0 D100=2, M100 giữ |
| Mất kênh AI event (AI Camera mode) | **FAULT** | FAULT | M104=1 M103=0 D100=4, M100 giữ |
| AI lỗi / không có kết quả > 3 s | **FAULT** | FAULT | M104=1 M103=0 D100=4 |
| PLC mất kết nối | (giữ) | FAULT (PLC disconnected) | tự nối lại rồi ghi lại toàn bộ |
| STOP SYSTEM | STOPPED | STOPPED | M103=0 M101=0 D100=5 |
| Vừa START (≤ 10 s) | STARTING | STARTING | giữ trạng thái trước |

## 10. Event / Snapshot / Log

* **Event History** (SQLite `events/events.db`, tab Events, export CSV): `PERSON_ENTERED/LEFT <ROI>`, `AREA_OCCUPIED/CLEAR`,
  `FAULT/FAULT_CLEARED`, `SYSTEM_START/STOP`, `CAMERA`, `PLC`, `AI`.
* **Snapshot** khi ROI chuyển CLEAR→OCCUPIED: `events/2026-09-15/09-32-01_ROI_001_PERSON.jpg` (bật/tắt trong `app_config.json`).
* **Log** `logs/YYYY-MM-DD.log`: `timestamp | level | module (CAMERA/AI/ROI/PLC/SYSTEM) | message`, ví dụ `PLC | M100 -> ON`.

## 11. File cấu hình (`config/`)

`app_config.json` (log level, snapshot, màu visualization), `camera_config.json`, `ai_config.json` (detector + logic/debounce),
`plc_config.json`, `roi_config.json`. File thiếu/hỏng → tự tạo mặc định, key lạ bị bỏ qua. Mọi nút **Apply & Save**
ghi ngay ra JSON; khi đóng app cũng lưu.

## 12. Kiểm thử

```bat
pip install -r requirements-dev.txt
python -m pytest tests -q
```

33 test: geometry (point-in-polygon lõm, clipping, intersection ratio), debounce/state machine, ROI processor + exclusion,
MC Protocol frame Binary/ASCII, driver thật ↔ `tools/mc_plc_simulator.py`, PLC mapper (mutual exclusion, fail-safe,
chỉ ghi khi đổi, heartbeat), config round-trip.

### Acceptance test (đã chạy tự động headless với video có 4 người)

| # | Kịch bản | Kết quả |
|---|---|---|
| 1 | Không người → AREA CLEAR, M100=OFF, M101=ON | ✔ |
| 2 | Người vào ROI → sau ON delay AREA OCCUPIED, M100=ON | ✔ (0.24 s) |
| 3 | YOLO mất 1–2 frame → M100 vẫn ON | ✔ (unit test PENDING_CLEAR) |
| 4 | Người rời ROI → sau OFF delay M100=OFF | ✔ (1.03 s) |
| 5 | Người ngoài ROI → M100=OFF | ✔ |
| 6 | Người trong exclusion → IGNORED, M100=OFF | ✔ |
| 7 | 2+ người, ≥1 trong ROI → M100=ON | ✔ |
| 8 | Camera disconnect → FAULT, M104=ON, không tự CLEAR | ✔ |
| 9 | PLC disconnect → UI không treo, báo PLC DISCONNECTED, tự nối lại | ✔ |
| 10 | Video file thay camera – toàn bộ pipeline | ✔ |

Chế độ **AI Camera** (chạy với mock provider + qua UI thật):

| # | Kịch bản | Kết quả |
|---|---|---|
| 1 | Kênh event ONLINE, không alarm → AREA CLEAR, M100=OFF M101=ON | ✔ |
| 2 | Camera báo người vào vùng → sau ON delay AREA OCCUPIED | ✔ |
| 3 | PLC: M100=ON, M200=ON (Robot Zone), D100=1 | ✔ |
| 4 | Hai người vào, một người ra → vẫn OCCUPIED (đếm người) | ✔ |
| 5 | Người cuối rời vùng → sau OFF delay AREA CLEAR, M100=OFF | ✔ |
| 6 | Event mục tiêu là **vehicle** → bỏ qua, M100 vẫn OFF | ✔ |
| 7 | Hai vùng độc lập: M200 ↔ M201 không ảnh hưởng nhau | ✔ |
| 8 | Mất kênh event → FAULT, M104=ON, M100 **giữ nguyên**, không CLEAR giả | ✔ |
| 9 | Kênh event trở lại → hết FAULT | ✔ |
| 10 | Đổi sang PC AI / YOLO rồi quay lại: kênh event tắt/bật đúng | ✔ |
| 11 | Camera không tới được (IP sai) → worker vẫn sống, UI không treo | ✔ |

## 13. Troubleshooting

| Vấn đề | Cách xử lý |
|---|---|
| `Cannot open USB camera index 0` | Camera đang bị app khác dùng; thử index khác, backend `msmf`; nút Scan. |
| RTSP `Cannot open stream` | Kiểm tra URL bằng VLC; đúng path theo hãng; bật RTSP/ONVIF trong camera; mật khẩu có ký tự đặc biệt → dùng ô RTSP URL với URL-encoding (`@` → `%40`); thử `udp`. |
| RTSP trễ / giật | Dùng sub-stream, transport TCP, dây mạng riêng. AI luôn xử lý frame mới nhất nên không tích lũy trễ. |
| `Model not found` | Đặt `.pt` vào `models/` hoặc để `auto_download = true` có internet. |
| `CUDA requested but not available` | App tự chạy CPU. Cài torch CUDA đúng driver. |
| FPS AI thấp trên CPU | Dùng `yolo11n`, imgsz 480/416, giảm FPS camera, hoặc GPU. |
| PLC `end code 0xC050` | ASCII/Binary không khớp giữa app và GX Works. |
| PLC `0xC059` / `0xC05C` | CPU không hỗ trợ lệnh; bit-unit lên word device; kiểm tra device tồn tại (M range, D range). |
| PLC ghi được khi STOP nhưng không khi RUN | Bật *Enable online change (MC Protocol)* / permit write during RUN. |
| Connect PLC timeout | Ping IP; cùng subnet; firewall Windows; port đúng SLMP connection; PLC đã reset sau khi write parameter. |
| Test Event mở được nhưng im lặng | Camera chưa bật rule AI, hoặc chưa tick *Notify Surveillance Center*. Xem RAW EVENT để biết camera có gửi gì không. |
| Event có nhưng không kích PLC | Kiểm tra cột *Target* trong Event Monitor: vehicle/animal bị bỏ theo thiết kế. Và region id đã map PLC device chưa. |
| Vùng không bao giờ CLEAR | Camera không gửi `inactive`: giảm *Clear timeout*. Nếu dùng enter/exit mà thiếu một EXIT thì vùng giữ OCCUPIED (đúng thiết kế fail-safe). |
| `401` khi Test Camera | Sai user/mật khẩu, hoặc tài khoản không có quyền remote. App tự thử digest rồi basic. |
| `403` khi mở kênh event | Bật ISAPI / Open Platform trong camera và cấp quyền cho tài khoản. |
| Basler/Hikrobot "SDK not installed" | Cài SDK + package Python tương ứng (mục 5.4), chạy lại app. |
| Video hết → FAULT | Bật Loop hoặc Restart – hệ thống cố ý không báo CLEAR khi mất nguồn hình. |
| ROI vẽ xong nhưng mất khi mở lại | Nhấn **Save ROI** (nút hiển thị `*` khi chưa lưu); khi đóng app cũng tự lưu. |

## 14. Lộ trình

* **Phase 1 (done)**: PySide6 UI (light industrial theme + thanh 5 bước), USB/Video/RTSP, YOLO person, polygon ROI + exclusion, foot-point logic, debounce/state
  machine, PLC simulation, MC Protocol 3E Binary/ASCII, M100/M101/…/D100, heartbeat, fail-safe, log, config JSON, SQLite
  event, snapshot, I/O test, multiple ROI + per-ROI device, tracking (ByteTrack, cần `lap`).
* **AI Camera mode (done)**: RTSP video, Hikvision ISAPI alertStream, chuẩn hoá event, lọc human,
  đếm người, debounce, map region → PLC, Event Monitor + RAW EVENT, simulation, fail-safe từng kênh.
* **Phase 2**: kiểm thử adapter Basler / Hikrobot / GenICam và camera Hikvision/Dahua thật, ONVIF discovery, thống kê.
* **Phase 3**: Modbus TCP / Siemens S7 / OPC UA / EtherNet/IP (kế thừa `BasePLC`), đa camera, report.
