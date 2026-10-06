# Tạo gói cập nhật nhỏ cho khách

Trên máy phát triển, bấm **`Tao-Update.bat`** ở thư mục gốc project. Script tự chạy test, build hai EXE, so sánh với các bản khách có thể đang dùng, tạo bản vá và kiểm tra dựng lại từng file. Chờ dòng **TAO UPDATE THANH CONG**.

File gửi khách nằm trong `release/VisionGuard-GPU-Update-<phiên bản>.zip`. Chỉ gửi ZIP update này. File `.sha256`, `.manifest.json`, `.validation.json` và log để người phát triển đối chiếu.

Khách giải nén vào thư mục riêng, đóng VisionGuard rồi chạy `Update.bat` trong gói. Nhập đường dẫn thư mục đang chứa `VisionGuard.exe`. Updater kiểm tra SHA-256, sao lưu file cũ, áp dụng bản vá và phục hồi file đã thay nếu có lỗi. Cấu hình, model, lịch sử và log của khách được giữ nguyên.

**Những file phải giữ trên máy build**

- Bản đầy đủ gốc và các ZIP update đã gửi khách. Mặc định bản đầy đủ là `release/VisionGuard-GPU-2026-10-01.zip`; nó chỉ là đầu vào so sánh, không được chép vào ZIP update.
- `build/update_config.json`: đường dẫn baseline, hai update ban đầu, release notes và trần dung lượng.
- `release/update-history.json`: script tự bổ sung sau mỗi lần tạo update thành công; đây là các phiên bản tiếp theo mà khách có thể đã cài.
- `build/runtime-lock.json`: phiên bản Python, Torch/CUDA, Qt, OpenCV và bộ build phải khớp. Script từ chối build nếu các phiên bản này bị đổi.

Gói mới hỗ trợ bản đầy đủ và mọi trạng thái update đã đăng ký, gồm cả update cũ chỉ chứa delta. Vì vậy khách có thể đi thẳng từ một phiên bản được hỗ trợ lên gói mới mà không phải cài từng ZIP trung gian.

**Các lệnh thường dùng**

```bat
Tao-Update.bat
Tao-Update.bat --version 2026-10-06-r1
Tao-Update.bat --bundle build\dist-update\VisionGuard --version 2026-10-06-r2
```

`--bundle` đóng gói một bundle đã build sẵn; test vẫn chạy. Khi chạy tự động, đặt `VG_NO_PAUSE=1` để batch trả exit code ngay. Mỗi phiên bản có tên riêng và không ghi đè ZIP đã tạo.

**Khi có lỗi**

- Test thất bại: sửa lỗi trước khi tạo gói. Không gửi artifact của lần thất bại.
- Thiếu ZIP gốc/update cũ: phục hồi file tương ứng hoặc cấu hình baseline đúng với khách đang hỗ trợ.
- Dependency thay đổi: dùng lại môi trường có phiên bản khớp `runtime-lock.json`. Không nâng Torch/CUDA giữa các bản sửa mã nguồn thông thường.
- ZIP vượt 128 MiB: script dừng, chưa công bố gói cho khách. Kiểm tra thư viện bị đổi. Một lần nâng runtime/model thực sự có thể cần gói lớn riêng; binary delta không bảo đảm mọi thay đổi đều nhỏ.
- Khách báo bản không tương thích: lấy đúng phiên bản của khách làm baseline, tạo lại gói có hỗ trợ phiên bản đó; không bỏ qua kiểm tra hash.

`Tao-Update.bat` ưu tiên Python ở `build/venv-update/`, sau đó `build/venv-gpu/`, cuối cùng Python trên PATH. Môi trường hiện có dùng `venv-gpu`; bản khóa và kiểm tra runtime giúp phát hiện các thay đổi thư viện ngoài ý muốn. Việc chuyển sang môi trường hoàn toàn cách ly cần build và kiểm tra tương thích runtime trước khi thay baseline đang phục vụ khách.

**Dọn dữ liệu build**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/clean_project.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File tools/clean_project.ps1 -Apply
```

Lệnh đầu chỉ liệt kê. Lệnh `-Apply` lưu metadata/dữ liệu site của các bộ test cũ vào ZIP kiểm toán, rồi dọn cache và work/staging đã xác định. Script kiểm tra đường dẫn và junction, không xóa bản đầy đủ, các ZIP update, model nguồn hoặc cấu hình đang dùng. Dừng app/test/build trước khi dọn.

Đo tải mô phỏng ứng dụng, không kết nối phần cứng:

```bat
python tools\benchmark_runtime.py --seconds 60 --cameras 4 --media --maximize-one --output release\benchmark.json
```

Benchmark này đo UI/pipeline/lưu trữ với ảnh và detector giả; không đại diện cho tốc độ YOLO hay giải mã RTSP trên máy khách.
