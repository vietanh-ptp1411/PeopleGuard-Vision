Hai model duoc giu lai, do tren GTX 1650 voi frame that tu camera 2688x1520.
Ten da doi thanh model1/model2 de ngoai hien truong khong phai doc ten ky thuat.

model1.pt  = yolo26s.pt  (Ultralytics YOLO26 small, 10.0M tham so, 22.8 GFLOPs)
             Dang dung. imgsz 1280 -> 42 ms, chiem 64% ngan sach 1 camera @15fps.

model2.pt  = yolo26m.pt  (Ultralytics YOLO26 medium, 21.9M tham so, 75.4 GFLOPs)
             Du tru cho may RTX 4060. Tren GTX 1650: 94 ms @1280 (vuot ngan sach),
             57 ms @960.

Ca hai tai tu https://github.com/ultralytics/assets/releases/download/v8.4.0/
Giay phep AGPL-3.0 (Ultralytics) - can Enterprise License neu ban cho khach.

Muon lay lai ban goc:
  .venv\Scripts\python.exe -c "from ultralytics.utils.downloads import attempt_download_asset as d; d('models/yolo26s.pt')"
