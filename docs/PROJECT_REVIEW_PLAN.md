# Review và kế hoạch tối ưu VisionGuard

Ngày rà soát: **05/10/2026**. Phạm vi: mã nguồn và working tree hiện tại, cấu trúc đóng gói, luồng camera → AI → ROI → PLC → giao diện/lưu trữ.

Cập nhật 06/10/2026: phần triển khai và kết quả kiểm thử được ghi riêng tại [Kết quả tối ưu](OPTIMIZATION_RESULTS.md); hướng dẫn dùng file `.bat` tạo update nằm tại [Tạo update](TAO_UPDATE.md). Nội dung dưới đây giữ nguyên làm mốc review ban đầu.

**Kết luận:** nền tảng hiện tại có thể tiếp tục phát triển. Ưu tiên sửa vòng đời worker, kiểm tra dữ liệu từng camera, thao tác chặn giao diện và giới hạn bộ nhớ trước khi tinh chỉnh model hoặc đổi kiến trúc lớn. Dọn build giúp giảm dung lượng ổ đĩa; giảm RAM/CPU cần sửa luồng chạy và đo trên cùng tải đầu vào.

Đây là báo cáo và kế hoạch triển khai. Lần rà soát này không sửa logic vận hành, cấu hình thiết bị hay xóa dữ liệu/build. Working tree đã có 17 file tracked được sửa và các script/test mới chưa được Git theo dõi; đó là công việc đang có, không phải file rác.

**1. Bằng chứng đã thu thập**

| Hạng mục | Kết quả |
|---|---|
| Test hiện có | `57 passed, 6 warnings, 31 subtests passed in 23.30s` |
| Cách chạy | PowerShell: `$env:QT_QPA_PLATFORM='offscreen'; python -m pytest tests -q -p no:cacheprovider` |
| Warnings | DeprecationWarning về kiểu SWIG; không làm test thất bại |
| Môi trường | Windows, Python 3.12.10; máy rà soát có 4 lõi vật lý, 8 luồng, RAM 15,92 GiB |
| Thư viện đang cài | PySide6 6.11.1, OpenCV 5.0.0.93, NumPy 2.5.1, Ultralytics 8.4.164, Torch 2.11.0+cu128 |
| Cấu hình đang lưu | 1 camera RTSP, `pc_yolo`, `model2.pt` khoảng 42,21 MiB, `imgsz=960`, AI tối đa 8 FPS/camera, UI tối đa 30 FPS |
| Tính năng tốn bộ nhớ bổ sung | Snapshot, clip, tracking hiện đều tắt; FP16 cũng tắt |
| Dung lượng logic | `build/` khoảng 10,76 GiB; `release/` khoảng 16,95 GiB; tổng khoảng 27,71 GiB |
| Cache mã nguồn | Khoảng 1,32 MiB `.pyc`, không tính các bundle/venv; dọn cache có lợi nhỏ về dung lượng |

Đã đọc mã, chạy test và các thí nghiệm nhỏ với dữ liệu giả. Không kết nối camera/PLC thật, không chạy benchmark YOLO với camera thực, không đo tải liên tục 8–24 giờ. Các ngân sách tài nguyên dưới đây là mục tiêu nghiệm thu ban đầu, chưa phải hiệu năng đã đạt. Dung lượng file là tổng kích thước logic, có thể khác dung lượng vật lý được giải phóng trên ổ đĩa.

**2. Những thiết kế tốt cần giữ**

- `LatestFrameBuffer` chỉ giữ một frame mới nhất; không tích lũy toàn bộ video chờ inference.
- Một model phục vụ các camera theo lượt; có giới hạn FPS inference riêng từng camera.
- Preview đã có giới hạn FPS và cơ chế xác nhận đã nhận frame. Không cần xây lại từ đầu phần này.
- Camera, inference và PLC có worker riêng. PLC chỉ ghi khi trạng thái thay đổi, ngoại trừ heartbeat.
- RTSP đã cấu hình số luồng decode; hiện là 1. Cần đo trước khi đổi.
- Recorder chỉ được tạo khi bật ghi clip. Dọn dữ liệu theo thời gian đã chạy nền.
- Có test ROI, PLC, UI và cập nhật phần mềm; updater bảo vệ cấu hình/model/dữ liệu tại nơi triển khai.

**3. Phát hiện cần xử lý, theo ưu tiên**

P0: độ đúng và độ ổn định phải xử lý trước khi giảm tải. P1: ảnh hưởng trực tiếp đến độ mượt, RAM hoặc quá trình dừng/chuyển chế độ. P2: hoàn thiện tính năng, khả năng bảo trì và đóng gói.

| ID | Mức | Phát hiện và tình huống kích hoạt | Vị trí chính | Hướng xử lý |
|---|---|---|---|---|
| F01 | P0 | Watchdog AI dùng thời điểm kết quả mới nhất của cả nhóm; một camera còn ra kết quả có thể che việc camera khác đã ngừng inference. Trong AI Camera, sức khỏe detector chỉ dựa vào event channel camera chính, dù đã có `_channel_states` cho cả nhóm. Hiện cấu hình 1 camera nên lỗi nhóm xuất hiện khi tăng camera. | `system_controller.py:941`, `:1074`, `:1101` | Theo dõi thời điểm frame/kết quả bằng monotonic riêng mỗi camera đang giám sát; tổng hợp health tất cả event channel. Kiểm tra dữ liệu thiếu, dữ liệu cũ và hồi phục riêng từng camera trước khi tiếp tục heartbeat/trạng thái hợp lệ. |
| F02 | P0 | Giảm số camera: worker bị bỏ khỏi danh sách rồi chỉ `wait(1500)` mà không kiểm tra kết quả. RTSP có thể chưa thoát. `set_sources()` còn thay các danh sách inference trực tiếp từ UI trong lúc worker sử dụng chúng. | `system_controller.py:215`, `:252`; `inference_worker.py:164` | Thực hiện đổi nhóm qua lệnh của inference worker; có xác nhận dừng. Giữ tham chiếu worker đang thoát, loại signal cũ bằng mã phiên cấu hình, giải phóng sau khi thực sự kết thúc. Tránh chờ tuần tự trên UI. |
| F03 | P1 | Pipeline cũ vẫn bị ROI manager giữ qua bound-method listener sau khi giảm số camera; tăng/giảm nhiều lần tích lũy object và callback. | `logic/pipeline.py:58`; `roi/roi_manager.py:39`; `system_controller.py:219` | Thêm lifecycle `close()` gỡ listener; gọi khi hủy pipeline sau khi inference đã ngừng sử dụng. Test lặp thay nhóm và kiểm tra weakref/listener. |
| F04 | P1 | `_log_event()` gọi SQLite INSERT + commit đồng bộ trên UI. Housekeeping dùng cùng lock/connection, còn `VACUUM` bên trong lock; khi dọn DB, UI có thể chờ khóa. | `system_controller.py:1234`; `event_repository.py:72`, `:111` | Tách StorageWorker sở hữu connection, queue có giới hạn, commit theo lô nhỏ. Thử WAL với connection đọc riêng; UI không chờ Future. Prune từng lô, lên lịch compact phù hợp, flush có xác nhận khi đóng. Queue đầy phải báo lỗi rõ, không âm thầm mất sự kiện. |
| F05 | P1 | Mỗi kết quả inference phát `PipelineResult` có tham chiếu ảnh qua Qt signal; đường này chưa có giới hạn như preview. UI bị chặn lâu có thể giữ nhiều ảnh và xử lý trạng thái cũ. | `inference_worker.py:221`; `logic/pipeline.py:26`; `system_controller.py:941` | Tách dữ liệu vẽ chỉ giữ bản mới nhất khỏi sự kiện chuyển trạng thái cần giữ. Gửi metadata gọn, chỉ giữ ảnh khi thật sự cần snapshot; không bỏ PERSON_ENTERED/LEFT để tiết kiệm RAM. |
| F06 | P1 | Snapshot copy ảnh rồi submit vào executor một worker, không có giới hạn backlog. Khi đĩa chậm/burst nhiều ROI, ảnh chờ chiếm RAM. Tên ảnh chỉ chính xác đến giây có thể trùng. | `snapshot_saver.py:26`, `:52` | Kiểm tra ngân sách trước khi copy; giới hạn số job và tổng byte, ghi rõ snapshot bị bỏ nếu quá tải. Dùng tên có camera + microsecond/sequence; báo kết quả ghi thực tế. Chính sách flush/cancel rõ ràng khi đóng. Hiện snapshot tắt. |
| F07 | P1 | Clip có queue 120 frame, pre-roll giới hạn theo thời gian nhưng chưa theo byte. `submit()` resize/copy trên thread gọi, thực tế từ callback UI. `clip.fps` chỉ đặt tốc độ VideoWriter, chưa dùng để lấy mẫu đầu vào. | `clip_recorder.py:38`, `:71`, `:144`, `:151`; `system_controller.py:909` | Tách đường ghi khỏi preview UI, lấy mẫu theo timestamp trước bước tốn chi phí, resize ở worker; giới hạn byte tổng cho queue + pre-roll. Giới hạn chiều rộng ảnh ghi. Kiểm tra thời lượng phát lại khi FPS đầu vào thay đổi. Hiện clip tắt. |
| F08 | P1 | Chuyển từ YOLO sang AI Camera chỉ dừng inference, không unload model. Một số lệnh Load có thể bị xếp hàng lặp lại. | `system_controller.py:508`, `:626`; `inference_worker.py:53`; `yolo_detector.py:144` | Có chế độ giữ model để đổi nhanh hoặc giải phóng khi đổi lâu; mặc định tiết kiệm bộ nhớ cho AI Camera. Hợp nhất yêu cầu load cùng cấu hình, có trạng thái loading/unloading và xác nhận hoàn tất. |
| F09 | P2 | Tile bị ẩn khi phóng to camera khác vẫn nhận frame và copy toàn ảnh thành QImage. UI đang copy ở độ phân giải nguồn. | `video_grid.py:122`, `:217`; `video_view.py:147`; `utils/qt_image.py:10` | Ngừng chuyển đổi ảnh cho tile ẩn/minimize, giữ tham chiếu frame mới nhất để vẽ khi hiện lại; cân nhắc ảnh preview theo kích thước tile. Giữ đầy đủ tọa độ gốc để ROI đúng. |
| F10 | P2 | Controller 1.252 dòng và cửa sổ chính 956 dòng gộp nhiều trách nhiệm. `EventsWidget`, `LogWidget` và Qt log bridge hiện không thấy được nối vào UI; README còn mô tả History/System Log. | `app/controllers/system_controller.py`; `app/main_window.py:257`; `app/widgets/events_widget.py`; `app/widgets/log_widget.py` | Tách theo trách nhiệm sau khi có test hành vi. Hoàn thiện History trong trang Lưu trữ với tải bất đồng bộ/phân trang, tận dụng EventsWidget. Với log viewer cũ, gỡ code nếu chốt chỉ mở file log; sửa README theo UI thực. |
| F11 | P2 | Requirements chỉ đặt cận dưới; venv build kế thừa thư viện máy qua `include-system-site-packages=true`. `.gitignore` bỏ sót build có hậu tố ngày. | `requirements.txt`; `build/venv-gpu/pyvenv.cfg`; `.gitignore` | Tạo môi trường build cách ly; khóa bộ dependency đã kiểm thử, ghi manifest phiên bản. Tách gói AI Camera / YOLO CPU / YOLO GPU; bổ sung ignore đúng phạm vi. |

Các đường dẫn rút gọn `system_controller.py` trong bảng là `visionguard/app/controllers/system_controller.py`; những file khác thuộc các package cùng tên dưới `visionguard/`, trừ đường dẫn build/config ở root.

**Bằng chứng tái hiện cho F01, F03, F06:**

- F03: tạo rồi bỏ tham chiếu ngoài của 30 `ProcessingPipeline`; sau `gc.collect()`, cả 30 còn sống qua listener. Gỡ các listener rồi thu gom lại: 0 còn sống. Đây là bằng chứng giữ object, chưa phải phép đo tốc độ tăng RSS của phiên vận hành dài.
- F01: gọi `_tick()` với controller giả, kết quả camera chính cũ 99 giây nhưng timestamp kết quả chung mới 0,1 giây; `_ai_error` vẫn rỗng. Không dùng camera/PLC trong thí nghiệm.
- F06: giữ tác vụ ghi giả đang bận, gửi thêm 12 ảnh nhỏ; đủ 12 tác vụ nằm chờ. Kết hợp kiểm tra code cho thấy không có giới hạn hàng đợi ở lớp SnapshotSaver. Không ghi ảnh ra đĩa trong thí nghiệm.

F02 cần stress test với reconnect thật/giả để xác định mọi tình huống crash; việc hủy QThread còn chạy là tình huống Qt nêu rõ không hợp lệ. Kế hoạch sử dụng xác nhận `finished` và quản lý vòng đời đối tượng. [Tài liệu Qt QThread](https://doc.qt.io/qt-6/qthread.html).

Với F06, `shutdown(wait=False)` không tự hủy các tác vụ đang chờ và không bảo đảm tiến trình thoát ngay khi còn Future. Cần thiết kế flush/cancel riêng. [Tài liệu Python 3.12 Executor.shutdown](https://docs.python.org/3.12/library/concurrent.futures.html#concurrent.futures.Executor.shutdown).

**Ước tính bộ nhớ clip từ cấu trúc hiện tại** — ảnh BGR uint8, scale 0,5 theo cả hai chiều, 30 FPS, pre-roll 2 giây:

| Nguồn | 120 ảnh trong queue | 60 ảnh pre-roll | Tổng ngân sách thô của hai vùng |
|---|---:|---:|---:|
| 1920×1080 | 178 MiB | 89 MiB | 267 MiB/camera |
| 3840×2160 | 712 MiB | 356 MiB | 1.068 MiB/camera |

Công thức: `width × height × 3 × scale² × số frame`. Đây là tính toán dung lượng khi các vùng đệm đầy, chưa tính ảnh nguồn, Qt, model, decoder, allocator và chưa phải RSS thực đo. FPS hoặc thời gian pre-roll khác sẽ làm số liệu khác. Vì vậy 120 frame chưa phải giới hạn RAM phù hợp cho mọi nguồn video.

**4. Kế hoạch dọn file, không làm mất bản phát hành**

| Nhóm | Dung lượng gần đúng | Xử lý dự kiến |
|---|---:|---|
| `.pytest_cache/`, `__pycache__/`, `.pyc` trong nguồn/test/tools/build scripts | Khoảng 1,32 MiB bytecode + pytest cache | Dọn khi không có test/build đang chạy; có thể tái tạo. Không quét xóa trong môi trường cài đặt/bundle theo wildcard toàn ổ đĩa. |
| `build/work-gpu/`, `build/work-gpu-2026-10-04/` | 0,762 GiB | Build trung gian; tái tạo được. Đưa vào nhóm cleanup sau khi xác nhận không có build hoạt động. |
| `release/.update-build-2026-10-02/` | 10,614 GiB | Chứa dist/work, bản cài test và gói đã giải nén. Giữ các báo cáo/manifest/cấu hình test cần đối chiếu, kiểm tra không có dữ liệu duy nhất rồi dọn thư mục tạm. |
| `release/.update-build-2026-10-04/` | 0,004 GiB | Tương tự: giữ bằng chứng xác minh rồi dọn staging. |
| `build/dist-gpu/` | 5,016 GiB | Bản build cũ; chỉ bỏ khi bản phát hành/baseline tương ứng đã được xác minh và không cần làm đầu vào bước đóng gói tiếp theo. |
| `build/dist-gpu-2026-10-04/` | 4,975 GiB | Giữ bản mới nhất phục vụ đối chiếu update; có thể đưa ra kho artifact sau khi quy trình build tái tạo được. |
| Hai ZIP GPU đầy đủ trong `release/` | Mỗi file khoảng 3,10 GiB | Giữ theo phiên bản. Hai SHA-256 ghi trong sidecar khác nhau; kiểm tra metadata ZIP thấy 4 member khác. Không coi là bản trùng chỉ vì gần bằng dung lượng. |
| ZIP update, manifest, SHA-256, release notes và validation | Khoảng 125 MiB ZIP update cộng metadata | Lưu cùng phiên bản/baseline để hỗ trợ cập nhật và quay lại bản trước. |
| `build/venv-gpu/` | Khoảng 0,011 GiB tại đây | Môi trường công cụ, không phải cache vô dụng. Thay bằng môi trường cách ly trong bước ổn định build. |
| `config/`, `models/model2.pt`, `events/`, `logs/`, mã camera SDK, tests | Dữ liệu và chức năng | Giữ; áp dụng retention cho runtime theo cấu hình. Không tự xóa model huấn luyện, lịch sử sự kiện hoặc mã adapter chỉ vì máy rà soát không sử dụng. |

**Có thể thu hồi khoảng 11,38 GiB từ work/staging** sau khi hoàn thành đối chiếu nói trên; thêm khoảng 5,02 GiB nếu bản dist cũ đủ điều kiện bỏ. Đây không phải cam kết sẽ xóa mọi thư mục trong bảng. Hai ZIP đầy đủ khác nhau ở tài liệu/installer và số member; phép so sánh đã làm dùng tên, CRC, kích thước trong ZIP, không thay thế kiểm tra SHA-256 từng file khi phát hành.

Quy trình cleanup cần có manifest liệt kê đường dẫn tuyệt đối, số file, số byte và lý do; mặc định dry-run, thao tác xóa chỉ trong allowlist. Kiểm tra target resolve vẫn thuộc workspace, từ chối junction/symlink dẫn ra ngoài, bỏ qua file Git đang theo dõi hoặc thay đổi đang làm. Sau cleanup, kiểm tra bundle và đường dẫn đầu vào update còn hợp lệ.

Các bổ sung `.gitignore` dự kiến:

```gitignore
/build/dist-*/
/build/work-*/
/build/*.zip.partial
```

`/release/` đã được ignore. Không dùng `/build/` vì thư mục này còn chứa spec, script đóng gói và updater. Không dùng `events/` thiếu dấu `/` đầu vì sẽ che cả source `visionguard/camera/events/`.

Giữ các file mới `build/make_update.py`, `build/update.ps1`, `build/vgdelta.py`, `tests/test_plc_worker.py`, `tests/test_update_package.py` và đưa vào commit chức năng tương ứng. Hàm `purge_old_clips()` chưa thấy caller và trùng nhiệm vụ với housekeeping là ứng viên gỡ sau khi kiểm tra entrypoint/API. Các widget thiếu wiring phải được phân loại theo tính năng trước khi xóa; riêng History cần được hoàn thiện.

**5. Lộ trình triển khai**

| Đợt | Công việc | Sản phẩm/điều kiện hoàn thành |
|---|---|---|
| 0 — Baseline và đóng băng thay đổi hiện có | Ghi manifest working tree; lưu baseline test; tạo video test cố định; thêm bộ đo tài nguyên theo PID và độ trễ UI/frame/PLC. Thống nhất ngân sách cảnh báo từ logic thực tế. | Báo cáo baseline tái chạy được cho 1 và nhiều camera, các chế độ/tính năng tách riêng. |
| 1 — Dọn workspace | Bổ sung ignore, script cleanup dry-run, lưu hồ sơ release; dọn cache/work/staging đủ điều kiện. | Danh sách chính xác file được dọn và dung lượng; source/test/build inputs đầy đủ. |
| 2 — Sửa độ đúng và vòng đời | F01–F03; cấu hình source qua worker command; timestamp per-camera; listener cleanup; bỏ callback từ phiên camera cũ. | Test camera im lặng trong nhóm, mất event channel phụ, đổi nhóm khi reconnect, đổi nhóm 100 lần đều đạt; số listener/thread trở lại mức nền. |
| 3 — UI và lưu trữ | F04–F07; StorageWorker, queue theo byte, ảnh snapshot/clip theo ngân sách, tách sự kiện khỏi ảnh preview. | Bơm burst sự kiện và giả lập đĩa chậm vẫn thao tác UI được; sự kiện quan trọng có xác nhận lưu; backlog không tăng vô hạn. |
| 4 — Tối ưu tải tính toán | F08–F09; cấu hình giới hạn thread/FPS có đo lường; bỏ chuyển đổi tile ẩn; benchmark imgsz và chế độ unload. | Có so sánh trước/sau trên cùng dữ liệu; độ trễ cảnh báo và chất lượng phát hiện đạt chuẩn đã chốt. |
| 5 — Hoàn thiện và phát hành | F10–F11; History, tài liệu, dependency lock, build cách ly, smoke test gói CPU/GPU/AI Camera, kiểm thử chuỗi update. | Build tái tạo được về phiên bản/phụ thuộc; cài và update giữ dữ liệu khách hàng; chạy bền 8–24 giờ đạt tiêu chí. |

Ưu tiên triển khai nhỏ theo từng đợt/commit. Chỉ tách các service như CameraGroup, HealthMonitor, StorageService, PLC output mapping khi hành vi liên quan đã có test; không cần thay toàn bộ GUI framework hay thêm nhiều process cho từng camera để đạt mục tiêu hiện tại.

**6. Cấu hình hiệu năng nên thử nghiệm**

| Thành phần | Hướng thử nghiệm | Ràng buộc |
|---|---|---|
| Preview | So sánh 20/25/30 FPS; tile ẩn ngừng convert; preview kích thước phù hợp màn hình | Không giảm FPS inference hoặc bỏ xử lý camera chỉ vì tile đang ẩn. |
| AI | Giữ baseline `model2.pt`, 960, 8 FPS/camera; so sánh 640/768/960 | Đánh giá người nhỏ/xa, che khuất và đi sát biên ROI trước khi hạ kích thước. |
| CPU | Đo ngân sách thread inference 1/2/4 trên máy mục tiêu, có xét số camera decode và GUI | Cấu hình thread trước warm-up; không mặc định dùng toàn bộ core hoặc ép 1 thread cho mọi máy. |
| GPU | FP16/ONNX/TensorRT chỉ là nhánh thử nghiệm sau khi baseline ổn định | Source hiện có ghi chú FP16 từng chậm trên GTX 1650; chưa xác minh lại phép đo đó trong review này. Không bật FP16 đại trà hoặc thay model tự động. |
| Snapshot | Chất lượng JPEG thử 85–90; tổng buffer ban đầu 32 MiB | Có thống kê lỗi/bỏ snapshot; không bỏ event tương ứng và không ghi path thành công khi ảnh thực tế thất bại. |
| Clip | Khởi điểm 10–15 FPS, chiều rộng tối đa 960–1280 tùy nhu cầu; queue theo byte | Thử ngân sách media toàn cục 128–256 MiB, khoảng 64 MiB/camera khi dùng 4 camera; giảm pre-roll/độ phân giải nếu vượt ngân sách, có thông báo rõ. |
| AI Camera | Không nạp YOLO khi mở; unload theo chính sách khi chuyển từ YOLO | Đo riêng lần mở mới và sau khi chuyển mode; giải phóng model không đồng nghĩa trả toàn bộ RAM runtime về hệ điều hành. |

Giới hạn thread PyTorch phải đặt trước khi chạy mã inference để có hiệu lực đúng. [Tài liệu torch.set_num_threads](https://docs.pytorch.org/docs/2.11/generated/torch.set_num_threads.html).

Khi unload, cần giải phóng tham chiếu model/tensor. `empty_cache()` chỉ trả phần cache GPU chưa dùng; nó không giải phóng tensor còn sống và không phải giải pháp chữa leak hay thao tác cần gọi mỗi frame. [Tài liệu torch.cuda.memory.empty_cache](https://docs.pytorch.org/docs/2.11/generated/torch.cuda.memory.empty_cache.html).

**Giảm FPS có thể làm báo động chậm hơn.** Logic hiện yêu cầu đồng thời ON delay 600 ms và 5 frame phát hiện liên tiếp. Riêng khoảng thời gian từ frame dương tính đầu đến frame thứ năm đã khoảng 500 ms ở 8 FPS, nhưng 1.000 ms ở 4 FPS, chưa kể thời gian chờ lấy mẫu, inference và PLC. Vì vậy phải đo từ người xuất hiện → trạng thái ROI → ghi PLC, không đánh giá tối ưu chỉ bằng CPU thấp.

**7. Tiêu chí nghiệm thu và cách đo**

| Nhóm | Mục tiêu đề xuất |
|---|---|
| UI | Độ trễ event loop p95 dưới 50 ms, p99 dưới 100 ms khi tải mục tiêu; các thao tác lưu/export/prune không tạo đoạn chặn UI kéo dài. Đo riêng lúc mở và load model. |
| RAM | Sau warm-up 10 phút, chạy cùng tải ít nhất 8 giờ; mức tăng median RSS từ cửa sổ đầu đến cuối không vượt `max(100 MiB, 5% baseline)`, không có xu hướng tăng đều chưa giải thích. Theo dõi thêm private bytes/commit của Windows và GPU allocated/reserved. |
| Buffer | Frame cho inference: 1/camera. Ảnh kết quả cho vẽ: tối đa bản mới nhất/camera. Media queue có trần byte thực sự và thống kê high-water/drop; sự kiện chuyển trạng thái có đường lưu riêng. |
| CPU | Đo CPU của process theo tổng năng lực CPU máy, lấy mẫu 1 giây; so sánh cùng video/model/FPS và tính năng. Mục tiêu ban đầu giảm ít nhất 20% CPU ở các kịch bản đã xác nhận có công việc thừa; chưa cam kết tỷ lệ cho mọi chế độ YOLO. |
| Chất lượng | Tập video cố định gồm người xa, che khuất, ROI chồng lấp, biên polygon và các camera khác nhau. Không có mất cảnh báo trong các ca chấp nhận; ghi rõ giới hạn của tập thử. |
| Độ trễ | Ghi timestamp capture, inference start/end, quyết định ROI, PLC request/ack. p95/p99 end-to-end đáp ứng thời gian phản ứng đã thống nhất; không dùng FPS cao để thay cho đo độ trễ. |
| Lifecycle | 100 lần START/STOP, thêm/bớt camera, đổi chế độ; không tăng listener, số worker, handle hoặc queue còn giữ. Đóng trong lúc load/reconnect không hủy QThread đang chạy. |
| Lỗi ngoại vi | Camera/event channel mất riêng lẻ, PLC timeout/từ chối ghi, đĩa chậm/đầy, DB bận, stream đổi độ phân giải. Không báo trạng thái hợp lệ bằng dữ liệu cũ; UI còn đáp ứng. |
| Build/update | Smoke test ứng dụng đóng gói trên máy sạch; kiểm tra từng baseline được hỗ trợ, lặp update, rollback khi file bị khóa và bảo toàn config/models/events/logs. |

Ma trận benchmark: 1/2/4 camera nếu cấu hình mục tiêu hỗ trợ; AI Camera, YOLO CPU, YOLO GPU; cửa sổ bình thường/phóng to một tile/minimize; snapshot/clip tắt và bật; mạng ổn định và mất kết nối. Có warm-up riêng, chạy ngắn 10–15 phút để so sánh cấu hình rồi mới chạy bền cấu hình được chọn.

Resource meter hiện đã đo CPU/RAM của máy và process nhưng chưa tạo được báo cáo này. Bổ sung CSV/JSON theo giây ở công cụ benchmark, không ghi log mỗi frame. Theo dõi riêng thời gian decode, độ tuổi frame, inference và độ trễ UI để biết phần nào thực sự cần tối ưu.

**Thứ tự khuyến nghị:** baseline → dọn build có kiểm soát → F01/F02/F03 → StorageWorker và giới hạn bộ nhớ → giảm công việc preview/model không cần thiết → hoàn thiện History/build → chạy bền trước khi phát hành.
