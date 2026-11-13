# Day 06 · Data Ingestion Engineering

**Lab cá nhân** · **4 giờ** · **Dữ liệu tự chọn**

Biến tài liệu thành kho tri thức có thể truy xuất cho AI agent, rồi đo xem cách chuẩn bị dữ liệu nào hiệu quả nhất.

| 📄 Dữ liệu | 🧩 Chiến lược | 🎯 Benchmark |
|---|---|---|
| Tối thiểu **30 trang nội dung** | Tối thiểu **3 cấu hình** | Tối thiểu **20 câu hỏi** |

## Bắt đầu nhanh

Dùng [starter PostgreSQL + pgvector](starter/README.md) để dựng database và hoàn thiện các hàm TODO, hoặc tự chọn vector store tương đương. Starter chỉ là khung: **bạn tự hiện thực pipeline và benchmark**.

## 1. Xây pipeline

![Đọc tài liệu, chuẩn hóa, chia chunk, tạo vector, lưu và kiểm tra](assets/ingestion-pipeline.svg)

Chọn tài liệu được phép sử dụng; giữ **ID tài liệu, nguồn, trang/vị trí** và metadata phù hợp. Dùng embedding model thật qua API hoặc chạy local; lưu và truy xuất bằng pgvector, Qdrant, Chroma hoặc một store tương đương.

## 2. So sánh và đánh giá

![Ba chiến lược được đánh giá trên cùng corpus và benchmark dev/test](assets/benchmark-workflow.svg)

Dùng cùng dữ liệu, model và câu hỏi cho cả ba cấu hình. Tách **15 câu dev / 5 câu test** trước khi chạy; chọn cấu hình trên dev, rồi đánh giá test sau khi cố định. Mỗi câu hỏi cần có đoạn bằng chứng và trang nguồn; thiết kế bộ câu hỏi và đánh giá theo [BENCHMARK.md](BENCHMARK.md).

Đánh giá theo năm khía cạnh: **độ chính xác truy xuất**, **tính mạch lạc của chunk**, **tính hữu dụng của metadata**, **chất lượng grounding** và **tác động của chiến lược dữ liệu**. Giải thích lựa chọn bằng kết quả, ví dụ lỗi và các đánh đổi.

## 3. Chuẩn bị bài nộp

1. Chạy pipeline và lưu cấu hình, kết quả top-3, trace cùng nhãn đánh giá thủ công.
2. Điền [thông tin học viên](STUDENT.md), [nhật ký thí nghiệm](artifacts/experiment_log.csv) và bản sao [mẫu báo cáo cá nhân](report/individual/MSSV_HoTen.md).
3. Kiểm tra mã nguồn, cách chạy, manifest dữ liệu và checklist trong [SUBMISSION.md](SUBMISSION.md).

## Tài liệu hướng dẫn

| Bạn cần tìm | Tài liệu |
|---|---|
| Setup baseline PostgreSQL + pgvector | [starter/README.md](starter/README.md) |
| Các bước làm và cách tính chỉ số | [GUIDE.md](GUIDE.md) |
| Tiến độ trong 4 giờ | [CHECKPOINTS.md](CHECKPOINTS.md) |
| Năm tiêu chí benchmark và bộ câu hỏi | [BENCHMARK.md](BENCHMARK.md) |
| Tiêu chí chấm điểm | [RUBRIC.md](RUBRIC.md) |
| Quy tắc sử dụng dữ liệu, AI và API | [RULES.md](RULES.md) |
| Cách nộp bài | [SUBMISSION.md](SUBMISSION.md) |

[data/format-example.json](data/format-example.json) chỉ minh họa định dạng, không phải corpus đủ điều kiện. Phần xử lý và benchmark do bạn tự hiện thực; không bắt buộc xây chatbot.

---

## Bài làm: Đặng Việt Anh (2A202603685)

Corpus: Bộ luật Lao động 45/2019/QH14 ([data/manifest.csv](data/manifest.csv)). Báo cáo: [report/individual/2A202603685_DangVietAnh.md](report/individual/2A202603685_DangVietAnh.md).

### Setup

```bash
python3 -m venv .venv && source .venv/bin/activate          # Python 3.12
pip install -r starter/requirements.txt sentence-transformers==6.3.0
cd starter
cp .env.example .env   # EMBEDDING_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
docker compose -p day6_student_starter up -d --wait         # PostgreSQL 16 + pgvector 0.8.7
python scripts/check_setup.py
python scripts/fetch_corpus.py                              # tải PDF theo manifest, kiểm sha256
```

Model chạy local trên CPU (lần đầu tự tải từ Hugging Face, ~470 MB), không cần API key, chi phí 0.
Phiên bản thư viện đầy đủ: [results/pip_freeze.txt](results/pip_freeze.txt).

### Chạy (từ `starter/`)

```bash
python -m src.run ingest                 # đọc, chuẩn hóa, chunk 2 index, embed, upsert; kiểm tra nạp lại không trùng
python -m src.run reingest-test          # giả lập tài liệu đổi: chỉ embed chunk đổi, xóa chunk cũ
python -m src.run verify-benchmark       # kiểm evidence nằm trên trang gold
python -m src.run retrieve --split dev   # chọn chiến lược trên dev
python -m src.run retrieve --split test
python -m src.run retrieve --split all
python -m src.run audit-sheets           # tạo bảng chấm coherence / metadata / grounding
python -m src.run audit-summary          # tổng hợp nhãn
```

| Thư mục / file | Nội dung |
|---|---|
| `starter/src/` | ingestion, chunking, embeddings, store (pgvector), evaluation, run, config |
| `benchmark/` | 21 câu (16 dev / 5 test), gold page + evidence, README tiêu chí |
| `artifacts/experiment_log.csv` | cấu hình A/B/C, giả thuyết, kết quả |
| `results/` | thống kê ingest, trace top-3 (`traces/`), `summary.md`, nhãn audit (`audit/`), log (`logs/`) |
