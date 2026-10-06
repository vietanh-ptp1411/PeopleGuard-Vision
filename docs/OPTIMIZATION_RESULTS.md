# Kết quả tối ưu — 06/10/2026

Đã triển khai các thay đổi vận hành, giới hạn bộ nhớ, lịch sử sự kiện và bộ tạo update nhỏ. Cấu hình camera/PLC/AI đang lưu và model của project được giữ nguyên; không tự giảm chất lượng phát hiện để lấy số CPU thấp hơn.

**Thay đổi đã làm**

- Kiểm tra kết quả AI riêng từng camera và tình trạng event channel cả nhóm. Kết quả inference bắt đầu trước STOP/START không được dùng cho phiên mới.
- Đổi danh sách nguồn qua hàng lệnh inference. Giữ worker đang dừng đến khi thực sự kết thúc; gỡ listener pipeline cũ, bỏ callback của camera đã thay thế.
- Mỗi camera có tối đa một kết quả AI đang chờ UI nhận. Khi UI bận, inference chờ có giới hạn thay vì tích lũy ảnh/kết quả; không bỏ chuyển trạng thái ENTER/CLEAR đã tạo.
- Hợp nhất yêu cầu load model; lệnh unload cũ không ghi đè yêu cầu load mới. Khi chuyển sang AI Camera, giải phóng model và cache GPU chưa sử dụng.
- SQLite chạy trên worker, hàng đợi tối đa 1.024 yêu cầu; ghi theo lô đến 32 sự kiện và dùng WAL. Không chạy VACUUM toàn DB trong lúc giám sát. Quá tải/lỗi lưu được báo rõ trên giao diện.
- Snapshot giới hạn mặc định 32 MiB/8 công việc, kiểm tra trước khi copy ảnh. Tên file không trùng trong cùng giây; ảnh ghi thất bại được bỏ đường dẫn khỏi lịch sử.
- Clip lấy mẫu trước khi resize, xử lý trên thread ghi riêng, nhận hình độc lập với preview. Mặc định tối đa 64 MiB/camera, cả nhóm 256 MiB bộ đệm frame; kích thước tối đa 1.280 px và tốc độ lấy mẫu tự động 15 FPS. Đổi độ phân giải tạo đoạn clip mới. Tốc độ phát lại theo timestamp.
- Tile bị ẩn giữ ảnh mới nhất nhưng không chuyển đổi QImage; khi hiện lại sẽ vẽ ảnh đó. Advanced AI cho phép chỉnh FPS/camera và số CPU thread; trang Lưu trữ có tùy chỉnh FPS, kích thước và bộ đệm clip.
- Lưu trữ → Lịch sử có phân trang, cập nhật sự kiện, xóa có xác nhận và xuất CSV ở worker, không tải toàn bộ lịch sử vào RAM.
- `.gitignore` nhận build theo ngày. Đã dọn **11,382 GiB dung lượng logic** cache/work/staging cũ. Hồ sơ và dữ liệu cần giữ được nén vào `release/cleanup-20261005-232514/preserved-metadata.zip` (~39,6 MiB); manifest nằm cùng thư mục. Bản đầy đủ và ZIP update được giữ.

**Tạo update**

Chạy `Tao-Update.bat`. Script kiểm tra phiên bản runtime, chạy test, build, kiểm tra mở/đóng EXE, tạo delta và xác minh nội dung dựng lại từ mỗi phiên bản khách được hỗ trợ. Chỉ công bố ZIP sau khi kiểm tra; lịch sử phát hành có cơ chế phục hồi khi tiến trình bị ngắt. Chặn gói lớn hơn 128 MiB mặc định để tránh vô tình gửi lại runtime lớn.

Khách chỉ nhận `VisionGuard-GPU-Update-<phiên bản>.zip`, giải nén vào thư mục riêng rồi chạy `Update.bat`. Không cần gửi model/CUDA đã có và không chép đè dữ liệu site. Xem [hướng dẫn đầy đủ](TAO_UPDATE.md).

**Gói đã tạo và thử cài**

`release/VisionGuard-GPU-Update-2026-10-06-r1.zip`: **3.237.061 byte = 3,09 MiB**. Tái sử dụng 4.966 file runtime; 9 delta phục vụ 3 file chương trình trên 3 trạng thái cũ, kèm 3 file hướng dẫn. Không chứa model, cấu hình hay dữ liệu site.

- Bản EXE cuối vượt qua kiểm tra khởi động và đóng bình thường với cấu hình tách biệt, PLC mô phỏng, không mở camera.
- Kiểm chứng dựng lại file từ bản đầy đủ 01/10, bản đã update 02/10 và bản đã update 04/10 đều đạt.
- Chạy updater thật trên thư mục cài đặt tạm cho từng trạng thái, rồi chạy lại lần hai: **6 lượt áp dụng thành công**, mỗi lượt đối chiếu **4.972 file** khớp SHA-256; các file kiểm chứng trong `config/models/events/logs` không đổi.
- Hash mã nguồn cuối khớp snapshot dùng build. Hướng dẫn trong ZIP có lưu ý chuyển đổi bit PLC cho khách chưa cài update 04/10.

SHA-256 của ZIP: `bee7e3d24abfd913b914b1d6db0360c213632df7d79a0cbc12535989fee18645`.

Báo cáo cạnh ZIP: `.validation.json` (dựng lại file), `.customer-validation.json` (thử cài thực tế), `.manifest.json` và `.zip.sha256`. `release/update-build-2026-10-06-r1.json` lưu phiên bản dependency và hash nguồn; log cùng tên đuôi `.log` lưu quá trình chạy `.bat`. Các thư mục cài đặt tạm đã được công cụ xóa sau khi kiểm tra.

**Kiểm chứng mã nguồn**

Lần chạy tổng cuối qua `Tao-Update.bat`: **103 test và 53 subtest đạt**, 6 cảnh báo deprecation SWIG từ dependency. Có test cho queue đầy/đĩa chậm, lỗi mở SQLite, snapshot thất bại, đổi nhóm camera lặp lại, đổi mode nhanh, dữ liệu cũ qua STOP/START, phân trang có sự kiện mới, update nối tiếp, bản vá hỏng, rollback khi file bị khóa và khôi phục quá trình xuất ZIP bị ngắt.

Test media ghi và đọc lại MP4 thật ở 6 tổ hợp FPS nguồn/đầu ra, kiểm tra thời lượng và khi đổi độ phân giải. Các test vận hành dùng detector giả và PLC mô phỏng.

Đã nạp `models/model2.pt` thật trên CPU, chạy 3 lượt với ảnh trống và unload thành công. Phép thử phát hiện Ultralytics đặt lại số CPU thread ở lần dựng predictor đầu; đã khôi phục mức giới hạn sau warm-up và thêm một test hồi quy. Chạy lại model thật xác nhận cấu hình 2 thread giữ đúng 2 sau 3 lượt inference. Báo cáo: `release/optimization-2026-10-05/detector-smoke.json`. Đây là kiểm tra backend, không phải đánh giá độ chính xác trên cảnh có người.

**Phép đo ban đầu**

Đo mô phỏng 4 camera, 720p, preview 20 FPS, inference giả 8 FPS/camera, bật snapshot/clip, phóng to một tile; mỗi bản chạy 30 giây, bỏ 5 giây đầu:

| Chỉ số | Trước | Sau ở lượt đo ban đầu |
|---|---:|---:|
| CPU trung bình, chuẩn hóa theo tổng CPU máy | 7,96% | 8,74% |
| RSS cao nhất | 154,34 MiB | 142,65 MiB |
| Độ trễ timer UI p95/p99 | 27/27 ms | 27/27 ms |

Nguồn số liệu: `release/optimization-2026-10-05/benchmark-before.json` và `benchmark-after.json`. Bộ đo không tính YOLO, RTSP decode hay độ trễ PLC vật lý; kết quả ngắn này cho thấy RAM giảm khoảng 7,6%, **chưa chứng minh CPU giảm**. Đây là lượt đo trước một số sửa lifecycle/media cuối cùng, không dùng để cam kết hiệu năng bản phát hành trên máy khách. Công cụ đo tái chạy nằm tại `tools/benchmark_runtime.py`.

**Giới hạn còn cần kiểm chứng tại nơi triển khai**

- Cần đo YOLO/RTSP thật và chạy bền 8–24 giờ để chốt mức CPU/RAM và thời gian cảnh báo trên phần cứng khách. Chưa có kết quả kiểm chứng này trong phiên làm việc.
- Giới hạn recorder áp dụng cho frame do recorder giữ/dự trù; bộ nhớ nội bộ encoder, decoder, model và ảnh UI thuộc phần khác. Khi hạ ngân sách, một resize đang chạy có thể giữ dự trù cũ đến khi kết thúc rồi được bỏ.
- Preview/inference giữ frame mới nhất có thể bỏ frame đầu vào cũ; cần chọn FPS đáp ứng thời gian phản ứng. Không tự hạ `imgsz`, ON delay hay số frame xác nhận của khách.
- Nếu đĩa chậm đến mức queue sự kiện đầy, yêu cầu mới bị từ chối và có báo lỗi; đây là giới hạn có chủ đích để RAM không tăng vô hạn, không phải bảo đảm lưu vô hạn khi ổ đĩa hỏng.
- Runtime hiện được kiểm tra bằng bản khóa phiên bản, vẫn dùng môi trường build cũ để giữ tương thích bundle khách. Việc chuyển hoàn toàn sang môi trường cách ly, tách installer AI Camera/CPU/GPU và tách sâu controller thành service là công việc tiếp theo; không cần làm lại runtime khách trong bản sửa này.
