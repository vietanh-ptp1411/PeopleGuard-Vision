# VisionGuard — Hướng dẫn chế độ YOLO

2026-09-17 · @u_9YvfNZMEnvXVXXsvRum8OQ

## Chế độ YOLO là gì

Chế độ **PC AI / YOLO** để máy tính tự phát hiện người từ luồng hình, không cần camera có AI. Model YOLO11n chạy trên PC, tìm người trong từng khung hình, rồi đối chiếu với vùng polygon bạn vẽ trên màn hình.

VisionGuard có hai chế độ và chúng **loại trừ nhau** — chọn một tại một thời điểm. Ở chế độ AI Camera, YOLO vẫn được cài nhưng nằm im.

|  | AI Camera (AcuSense) | PC AI / YOLO |
| --- | --- | --- |
| Ai phát hiện người | Camera | Máy tính |
| Camera cần gì | Bắt buộc có AI (Hikvision AcuSense…) | **Bất kỳ** — USB, file video, RTSP |
| Vẽ vùng ở đâu | Trong web camera | **Trên app**, kéo chuột trực tiếp |
| Vùng loại trừ | Không | **Có** |
| Đổi vùng mất bao lâu | Vào web camera, sửa, lưu | Vài giây, ngay trên hình |
| Độ trễ tới PLC | \~0,5–0,9 s | \~0,25 s |
| Tải lên máy tính | Gần như không | Cần GPU hoặc CPU khá |
| Mất mạng camera | FAULT | FAULT |

Chọn YOLO khi: chưa có camera AI, cần vùng hình dạng phức tạp, cần vùng loại trừ, hoặc cần đổi vùng thường xuyên. Chọn AI Camera khi PC yếu hoặc muốn giảm phụ thuộc vào máy tính.

## Yêu cầu hệ thống

Máy đang dùng đã đủ điều kiện, kiểm tra ngày 17/09/2026: GTX 1650 4GB, CUDA bật được, model đã có sẵn.

| Thành phần | Cần | Trên máy này |
| --- | --- | --- |
| Model | `models/yolo11n.pt` (5,6 MB) | Đã có |
| ultralytics | ≥ 8.0 | 8.4.115 |
| torch | ≥ 2.0 | 2.11.0+cu128 |
| GPU | Không bắt buộc | GTX 1650, 4,3 GB, CUDA = True |
| opencv-python | ✓ | Đã có |

Model là bản **pretrained** của Ultralytics, chỉ lọc class `person` (COCO class 0). **Không phải train gì cả.** Nếu file model bị xóa, app tự tải lại khi có mạng (`auto_download`).

### Nguồn hình dùng được

- **USB webcam** — cắm là chạy, hợp để thử nghiệm
- **File video** — chạy thử cả chuỗi mà không cần camera
- **RTSP / IP camera** — dùng thật, kể cả camera không có AI
- **Basler / Hikrobot / GenICam** — camera công nghiệp, cần cài SDK của hãng

Camera Hikvision AcuSense cũng dùng được ở chế độ này — chỉ lấy luồng RTSP, bỏ qua phần AI của nó.

## Chuyển sang chế độ YOLO

Một thao tác duy nhất: tab **Camera** → *Detection source* → chọn **PC AI / YOLO (software detects)**.

Giao diện tự đổi theo, không cần khởi động lại app:

|  | AI Camera | → YOLO |
| --- | --- | --- |
| Tab hiện | Overview, Camera, **AI Events**, PLC, History | Overview, Camera, **AI Model**, **Zones**, PLC, History |
| Chip trên command bar | CAMERA · AI EVENTS · REGIONS · PLC | CAMERA · **AI MODEL** · **ZONES** · PLC |
| Huy hiệu trên thanh đầu | `AI CAMERA` | `PC AI · YOLO` |
| Nhóm nút dưới khung hình | ZONES bị khóa | ZONES **mở khóa** |

Kênh sự kiện AI của camera sẽ chuyển sang `DISABLED` — đúng như mong đợi, vì ở chế độ này PC tự lo việc phát hiện.

App ghi lựa chọn vào `config/camera_config.json` khi thoát, lần sau mở lên vẫn ở chế độ này.

## Bước 1–2 — Nguồn hình và model

Hai bước này phải xong trước khi vẽ vùng, vì bạn cần thấy hình thật mới vẽ đúng chỗ.

### Bước 1 — Nguồn hình

Tab **Camera** → *Camera Type*, chọn một trong bốn:

1. **USB Camera** — đặt *Device Index* (thường là 0). Bấm **Scan** để app liệt kê các index đang có.
2. **Video File** — bấm **Browse**, chọn file `.mp4`. Bật *Loop* để phát lặp lại.
3. **RTSP / IP Camera** — điền IP, user, password. Với Hikvision, đường dẫn mặc định `/Streaming/Channels/101` đã đúng.
4. **Industrial** — Basler / Hikrobot / GenICam, cần SDK của hãng.

Sau đó: **Apply & Save** → **Connect** → **Start**. Nhanh hơn thì dùng ba nút **Connect / Start / Stop** ngay dưới khung hình.

Hình lên là đúng. Thanh **LIVE VIEW** phía trên khung hình sẽ hiện tên nguồn và số fps; chip **CAMERA** chuyển xanh kèm chữ *Đang truyền hình*.

### Bước 2 — Nạp model

Tab **AI Model** → **Load Model**. Mặc định app tự nạp khi mở lên, nên thường đã xong sẵn.

Nạp lần đầu trên GPU mất khoảng **5 giây** (biên dịch nhân CUDA), trên CPU khoảng **0,3 giây**. Những lần sau nhanh hơn.

Xong thì dòng *Model* hiện `yolo11n.pt | cuda:0` và chip **AI MODEL** chuyển sang *Đã nạp model*. Nếu ghi `cpu` mà máy có GPU, xem mục Xử lý sự cố.

## Bước 3 — Vẽ vùng giám sát

Đây là điểm mạnh lớn nhất của chế độ YOLO: vẽ polygon tự do trực tiếp trên hình, kèm vùng loại trừ.

Có hai loại vùng:

- **Zone (include)** — vùng giám sát. Có người trong đây thì bật bit PLC.
- **Exclusion** — vùng loại trừ. Người nằm trong đây **không tính**, dù đứng giữa vùng giám sát. Dùng cho lối đi an toàn, bục vận hành, khu vực ngoài hàng rào.

### Cách vẽ

1. Bấm **+ Zone** (dưới khung hình hoặc trong tab *Zones*).
2. Click từng điểm trên hình để tạo đỉnh polygon.
3. **Double-click** hoặc **Enter** để đóng vùng. Click phải để hoàn tác điểm vừa đặt, **Esc** để huỷ.
4. Lặp lại với **+ Exclusion** nếu cần vùng loại trừ.
5. Bấm **Save**.

### Sửa vùng đã vẽ

Bật nút **Edit** rồi:

- Kéo một đỉnh để di chuyển nó
- **Shift + click** lên cạnh để thêm đỉnh mới
- **Click phải** lên đỉnh để xoá đỉnh
- Kéo bên trong để dời cả vùng
- Phím **Delete** để xoá vùng đang chọn

Dòng gợi ý dưới khung hình luôn nhắc bạn đang ở chế độ nào và làm gì tiếp.

### Gán địa chỉ PLC cho từng vùng

Tab **Zones** → bảng *ROI LIST* → chọn một vùng → nhóm *SELECTED ROI*:

| Ô | Ý nghĩa |
| --- | --- |
| Name | Tên dễ đọc, hiện trong log và History |
| PLC device | Bit riêng cho vùng này, ví dụ `M200` |
| Enabled | Tắt tạm một vùng mà không phải xoá |

Ô *PLC device* không bắt buộc. Để trống thì vùng đó vẫn tính vào trạng thái chung (bit `M100`), chỉ là không có bit riêng. Địa chỉ sai sẽ báo đỏ ngay tại chỗ.

**Tọa độ vùng được lưu chuẩn hoá theo tỷ lệ 0–1**, nên đổi độ phân giải camera hay đổi nguồn hình thì vùng vẫn nằm đúng chỗ. File lưu ở `config/roi_config.json`.

## Bước 4–5 — PLC và chạy

### Bước 4 — PLC

Hai lựa chọn:

- **Chạy thử:** bật nút **PLC SIM** trên command bar. Mọi lệnh ghi vào bộ nhớ ảo, xem ở tab *PLC → Memory*. Không cần phần cứng.
- **Chạy thật:** tắt **PLC SIM**, vào tab **PLC → Connection**, nhập IP và Port, bấm **Apply & Save** rồi **Connect**.

Các bit mặc định gửi sang PLC:

| Bit | Ý nghĩa |
| --- | --- |
| `M100` | Có người (PERSON) |
| `M101` | Hết người (CLEAR) |
| `M102` | Camera OK |
| `M103` | AI đang chạy |
| `M104` | FAULT |
| `M110` | Heartbeat, đảo 0/1 mỗi 500 ms |
| `D100` | Status word |
| `M200`… | Bit riêng từng vùng, do bạn gán |

Bên PLC cần khai một **SLMP Connection** trong GX Works: giao thức TCP, port 5000, Communication Data Code **Binary**. Sai data code sẽ báo lỗi `0xC050`.

### Bước 5 — Chạy

Bấm **START** (hoặc **F5**). Dừng bằng **STOP** (**F6**). **F11** để toàn màn hình.

Khi chạy, khung hình sẽ vẽ khung bao quanh từng người và tô vùng giám sát.

### Đọc trạng thái

Băng lớn giữa command bar là thứ duy nhất cần nhìn từ xa:

| Băng | Nghĩa |
| --- | --- |
| `AREA CLEAR` (xanh lá) | Không có ai trong vùng |
| `PERSON DETECTED` (đỏ) | Có người |
| `FAULT` (vàng) | Mất camera hoặc mất PLC — bit PERSON **giữ nguyên**, không bao giờ tự báo hết người |
| `STOPPED` (xám) | Chưa bấm START |

Bốn chip bên phải cho biết khâu nào đang hỏng. Bấm vào chip để mở đúng tab xử lý. Có sự cố thì một dải cảnh báo hiện ngay dưới kèm nguyên văn lý do.

## Các tham số

Tab **AI Model** chỉ hiện 5 ô khi mở lên. Phần còn lại nằm sau **Advanced settings** vì chỉ cần lúc lắp đặt. Đây là ý nghĩa từng cái.

### Hiện sẵn — những ô bạn thực sự sẽ đụng

| Tham số | Mặc định | Ý nghĩa | Khi nào sửa |
| --- | --- | --- | --- |
| Confidence | 0,40 | Ngưỡng tin cậy tối thiểu để coi là người | Bỏ sót người → hạ xuống 0,30. Báo nhầm nhiều → nâng lên 0,50 |
| Device | auto | `auto` tự dùng GPU nếu có | Ít khi cần đổi |
| Containment mode | Foot Point | Xét người nằm trong vùng theo điểm nào | Xem bảng dưới |
| ON delay | 200 ms | Phải thấy người liên tục bừng này mới báo CÓ NGƯỜI | Tăng nếu bit PLC nhấp nháy |
| OFF delay | 1000 ms | Vùng phải trống bừng này mới báo HẾT NGƯỜI | Tăng nếu người bị che khuất chớp nhoáng |

### Containment mode — ô quan trọng nhất

Quyết định lấy điểm nào trên người để xét đã vào vùng hay chưa.

| Chế độ | Xét theo | Dùng khi |
| --- | --- | --- |
| **Foot Point** | Điểm chân (giữa cạnh dưới khung) | **Mặc định, đúng cho hầu hết trường hợp** — vùng vẽ trên mặt sàn |
| Center | Tâm khung | Camera nhìn từ trên xuống |
| Intersection | Tỷ lệ diện tích khung chồng lên vùng | Cần chặt hơn, kèm *Intersection threshold* |

Vẽ vùng trên mặt sàn thì **Foot Point** là đúng: người chỉ tính là vào khi chân đã bước vào.

### Sau Advanced settings — đừng đụng nếu chưa cần

| Tham số | Mặc định | Ý nghĩa |
| --- | --- | --- |
| IoU | 0,50 | Ngưỡng gộp các khung trùng nhau |
| Image size | 640 | Kích thước ảnh đưa vào model. Giảm → nhanh hơn, bỏ sót người ở xa |
| Tracking | tắt | Bám ID từng người qua các khung hình |
| FP16 (half) | tắt | Tăng tốc trên GPU, chỉ có tác dụng khi chạy CUDA |
| Auto load | bật | Tự nạp model khi mở app |
| Intersection threshold | 0,30 | Chỉ dùng khi containment mode = Intersection |
| Min detection frames | 3 | Phải thấy người ở bừng này khung hình liên tiếp mới tính |

**ON delay và Min detection frames cùng chống nhiễu** nhưng theo hai cách: một theo thời gian, một theo số khung. Cả hai phải thoả mãn thì mới báo có người.

## Hiệu năng thực đo

Đo ngày 17/09/2026 trên chính máy này, dùng detector của app trên ảnh thật, `imgsz = 640`, `confidence = 0,40`.

| Thiết bị | Ảnh 810×1080 (4 người) | Ảnh 1280×720 (2 người) |
| --- | --- | --- |
| GTX 1650 (CUDA) | 15,4 ms — **\~65 fps** | 13,7 ms — **\~73 fps** |
| CPU | 69,3 ms — \~14 fps | 55,1 ms — \~18 fps |

Kết quả nhận dạng giống hệt nhau trên GPU và CPU — CPU chỉ chậm hơn, không kém chính xác. Độ tin cậy các người được phát hiện: 0,62 – 0,89.

**14 fps trên CPU vẫn dùng được** cho bài toán này. Một người đi bộ di chuyển khoảng 10 cm giữa hai khung hình ở 14 fps — thừa để bắt kịp.

### Máy yếu thì chỉnh gì

Theo thứ tự hiệu quả:

1. **Dùng sub-stream của camera** thay vì main stream — `/Streaming/Channels/**102**` (1280×720 thay vì 2688×1520). Giảm tải giải mã rất nhiều mà gần như không ảnh hưởng độ chính xác ở khoảng cách gần.
2. **Giảm Image size** từ 640 xuống 480 — nhanh hơn rõ, đổi lại dễ bỏ sót người ở xa.
3. **Bật FP16** nếu chạy GPU.
4. **Tắt Tracking** nếu đang bật — bài toán này không cần bám ID từng người.

App luôn giữ **một khung hình mới nhất** để xử lý, không xếp hàng. Máy chậm thì khung hình bị bỏ bớt chứ độ trễ không dồn lại. Số khung bỏ hiện ở tab *Overview → PERFORMANCE → Frames dropped*.

## Xử lý sự cố

Bốn chỗ tra thông tin, theo thứ tự nên xem:

1. **Dải cảnh báo** dưới command bar — chỉ hiện khi có sự cố, ghi nguyên văn lý do kèm nút mở tab
2. **Chip trạng thái** — khâu nào đỏ thì bấm vào đó
3. **SYSTEM LOG** cuối cửa sổ, lưu ở `logs/`
4. **Tab History** — lịch sử sự kiện, xuất được CSV

| Triệu chứng | Nguyên nhân thường gặp | Xử lý |
| --- | --- | --- |
| Model nạp ra `cpu` dù có GPU | torch bản CPU-only | Đặt *Device* = `cuda` để thấy lỗi thật, rồi cài lại torch bản CUDA |
| Không bắt được người | Confidence cao quá, hoặc người quá nhỏ trong khung | Hạ confidence xuống 0,30; zoom camera vào vùng |
| Có khung bao người nhưng bit PLC không bật | Người ngoài vùng, hoặc bị vùng loại trừ che | Xem ô *IN ZONE* và *IGNORED* ở tab Overview |
| Bit PLC nhấp nháy | ON/OFF delay quá ngắn | ON delay lên 300 ms, OFF delay lên 1500 ms |
| Hình giật, fps thấp | Máy quá tải | Xem mục Hiệu năng ở trên |
| FAULT liên tục | Mất camera hoặc mất PLC | Đọc dải cảnh báo, nó ghi rõ khâu nào |
| Vùng vẽ xong rồi mất | Chưa bấm **Save** | Chip ZONES có chữ *chưa lưu* khi còn thay đổi chưa ghi |

### Thử mà không cần camera lẫn PLC

Đặt *Camera Type* = **Video File**, trỏ tới một clip có người đi lại, bật **PLC SIM**, vẽ một vùng rồi **START**. Toàn bộ chuỗi chạy thật, chỉ có nguồn hình và PLC là giả. Xem lệnh ghi ở tab *PLC → Memory*.

## Giới hạn và an toàn

**Đây là thiết bị giám sát, KHÔNG phải thiết bị an toàn đạt chuẩn.** Không dùng nó làm phương tiện bảo vệ người duy nhất. Bảo vệ người phải có rào chắn, cảm biến an toàn hoặc PLC an toàn được chứng nhận, chạy song song và độc lập.

Những gì hệ thống này **không làm được**:

- **Người bị che khuất** sau máy móc thì không phát hiện được. Đây là kiểu hỏng nguy hiểm nhất vì nó im lặng.
- **Người nằm, ngồi xổm hoặc bò** dễ bị bỏ sót hơn người đứng — model được huấn luyện chủ yếu trên người ở tư thế bình thường.
- **Thiếu sáng hoặc ngược sáng mạnh** làm giảm độ chính xác.
- **Không có giám sát đứt dây hoặc tự kiểm tra** như thiết bị an toàn thật.

Những gì hệ thống **có làm**, và đã kiểm chứng bằng bộ test tự động:

- Mất camera giữa chừng → vào **FAULT**, bật `M104`, **giữ nguyên** bit PERSON. Không bao giờ tự báo hết người khi mất nguồn tin.
- Mất PLC → báo FAULT, tự kết nối lại, giao diện không đứng hình.
- Heartbeat `M110` đảo 0/1 mỗi 500 ms để PLC tự biết phần mềm còn sống. **Nên viết logic giám sát heartbeat này trong ladder** — heartbeat đứng quá 2 giây thì coi như mất hệ thống.

### Trước khi bàn giao

Làm đủ bốn bước này trước mặt người nghiệm thu:

1. Đi vào vùng → bit `M100` bật, băng chuyển đỏ
2. Đi ra → sau OFF delay bit tắt, băng chuyển xanh
3. Đứng yên trong vùng 3 phút → bit **giữ ON liên tục**
4. **Rút dây camera** → FAULT, `M104` bật, `M100` **giữ nguyên trạng thái cũ**

Bước 4 là bước quan trọng nhất.
