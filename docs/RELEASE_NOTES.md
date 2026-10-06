VisionGuard — tối ưu vận hành và lưu trữ, 06/10/2026

- Theo dõi kết quả AI và kênh sự kiện riêng từng camera; phát hiện dữ liệu cũ của camera trong nhóm.
- Dừng worker và gỡ listener khi thay đổi số camera; giới hạn kết quả AI đang chờ giao diện.
- Ghi lịch sử SQLite ở worker riêng; thêm Lưu trữ → Lịch sử, phân trang và xuất CSV.
- Giới hạn bộ nhớ snapshot và clip; lấy mẫu video trước khi resize, ghi clip độc lập với preview.
- Tile camera bị ẩn không chuyển đổi ảnh; giải phóng model khi chuyển sang AI Camera.
- Giữ nguyên cấu hình, model huấn luyện và dữ liệu của khách khi cài gói cập nhật.

Sau cập nhật: mở phần mềm, kiểm tra các camera và thử người đi vào/ra từng ROI để xác nhận bit PLC.

Nếu máy khách chưa cài bản cập nhật 04/10/2026, bản này cũng bao gồm thay đổi giao diện PLC:

- Mỗi ROI gửi bit riêng: có người = 1, không có người = 0. Các bit chung PERSON/CLEAR/Alarm/Warning,
  Camera OK/AI Running/Fault/Status word của bản cũ không còn được ghi.
- Vào PLC → Devices, kiểm tra hoặc gán bit riêng cho từng ROI, Apply rồi Save ROI. Không cho START
  khi chưa có ROI hoặc ROI chưa gán bit hợp lệ.
- Kiểm tra Heartbeat trong PLC → Connection. Kiểm tra chương trình PLC và tắt các bit chung cũ
  không còn sử dụng; không để PLC tiếp tục dựa vào địa chỉ cũ để nhận cảnh báo.
- Kiểm thử người đi vào/ra từng vùng và đối chiếu bit PLC trước khi đưa lại vào vận hành.
