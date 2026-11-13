# Báo cáo cá nhân — Day 6

Đặng Việt Anh · 2A202603685 · L3A

## Dữ liệu và pipeline

- **Chủ đề, nguồn/quyền, số trang và quy tắc tính trang.**
  - Văn bản: Bộ luật Lao động số 45/2019/QH14, file PDF dạng Word. Nguồn là bản do Trường ĐH Ngân hàng TP.HCM đăng lại; đường dẫn và sha256 ghi trong [data/manifest.csv](../../data/manifest.csv).
  - Quyền sử dụng: văn bản quy phạm pháp luật không thuộc đối tượng bảo hộ quyền tác giả (Điều 15 Luật SHTT).
  - Số trang: 47 trang vật lý, **tính 46 trang** (1–46). Trang 47 bị loại vì chỉ có tên người ký (30 ký tự).
  - Quy tắc tính: số trang = số trang vật lý của file PDF; tổng 190.412 ký tự sau chuẩn hóa. Trang ngắn nhất vẫn trên 200 ký tự.
  - File PDF không commit (`.gitignore`); tải lại bằng `scripts/fetch_corpus.py`, script có kiểm sha256.
- **Chuẩn hóa, lỗi và giới hạn.**
  - Đọc bằng pypdf `extract_text()`, chuẩn hóa Unicode NFKC, bỏ ký tự vô hình và gộp khoảng trắng.
  - Đoạn văn được dựng lại từ các dấu cấu trúc: `Chương <La Mã>`, `Mục n.`, `Điều n.`, khoản `1.`, điểm `a)`. Code nhận đủ 17/17 chương và 220/220 điều. Bỏ 2 dòng trang trí (`-----`), có log trong `results/ingest_report.json`.
  - Không có trang scan nên không cần OCR.
  - Giới hạn:
    - Đây là bản đăng lại, không lấy từ cổng thông tin chính thống, và là nguyên văn năm 2019, **chưa gồm sửa đổi năm 2025**.
    - Nhãn khoản dạng `1b.`/`1c.` (Điều 219, trang 46) không được nhận diện.
    - Metadata của chunk lấy theo điều **tại vị trí bắt đầu** chunk.
- **Embedding, vector store, lệnh chạy và chi phí.**
  - Model: `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, revision `e8f8c21…`. Chạy local trên CPU, 384 chiều, `max_seq_length` 128, vector chuẩn hóa. Query và document được embed cùng một cách.
  - Vector store: PostgreSQL 16 + pgvector 0.8.7 qua Docker. Tìm kiếm cosine chính xác (`<=>`, không dùng index xấp xỉ).
  - Lệnh chạy: xem phần "Bài làm" trong [README.md](../../README.md). Chi phí 0 USD.
  - Phiên bản thư viện: [results/pip_freeze.txt](../../results/pip_freeze.txt) và trường `environment` trong `results/ingest_stats.json`.
  - Nạp lại cùng phiên bản không tạo bản ghi trùng: 492/616 dòng trước và sau, 0 chunk embed lại ([results/reingest_check.json](../../results/reingest_check.json)).
  - Tài liệu thay đổi được xử lý bằng `chunk_id` ổn định + `content_hash`. Thử nghiệm sửa một trang: 1 chunk embed lại, 32 giữ nguyên, 7 chunk cũ bị xóa ([results/reingest_changed_doc_test.json](../../results/reingest_changed_doc_test.json)).
- **Metadata.**
  - Trường bắt buộc: `document_id`, `source` (URL), `page`.
  - Trường bổ sung: `chapter` (INT), `chapter_title`, `section`, `article` (INT) và `article_title`. Các giá trị lấy trực tiếp từ heading trong văn bản và được mang qua ranh giới trang; không suy đoán giá trị.
  - Trường lọc: `chapter`, nằm trong allow-list SQL. Lý do chọn: Bộ luật chia theo chủ đề lớn, và cùng một khái niệm (làm thêm giờ, báo trước, thời giờ làm việc) lặp lại ở nhiều chương với quy định khác nhau. Người hỏi thường biết mình hỏi về chương nào, ví dụ lao động nữ hay lao động chưa thành niên.

## Cấu hình và benchmark

- **A / B / C.**
  - **A:** cửa sổ cố định 450 ký tự, overlap 50, theo từng trang, không lọc.
  - **B:** chunk theo đoạn/khoản, tối đa 450 ký tự. Mỗi `Điều n.` luôn mở chunk mới, nên một chunk không trộn hai điều. Đoạn dài hơn 450 ký tự được tách theo câu; cắt cứng tại khoảng trắng chỉ khi một câu tự nó dài hơn 450 ký tự. Không lọc.
  - **C:** dùng đúng index của B, chỉ thêm `WHERE chapter = n` khi câu hỏi có "Theo Chương N". **B–C chỉ khác nhau ở filter**; A–B chỉ khác nhau ở cách chunk. Cả ba dùng cùng model, store, corpus và benchmark.
  - Chọn 450 thay cho 500 ký tự trước khi chạy benchmark, vì tiếng Việt tốn nhiều token hơn. Ở mức 500 ký tự, 198/492 chunk A vượt 128 token và bị model cắt khi embed. Ở mức 450, con số này còn 25/492 (A) và 4/616 (B).
- **Benchmark.**
  - 21 câu trong [benchmark/questions.jsonl](../../benchmark/questions.jsonl): **16 dev / 5 test**. Loại câu: 7 trực tiếp, 2 diễn đạt lại, 6 có điều kiện, 6 metadata-filter (4 dev, 2 test).
  - Test chỉ lấy từ các chương không có câu dev nào: X, XI, XIV.
  - Evidence được kiểm chứng tự động: nằm trong text đã chuẩn hóa của trang gold, xuất hiện ở đúng 1 trang, và thuộc đúng chương ghi trong filter ([results/benchmark_verification.json](../../results/benchmark_verification.json)). Học viên đã đọc lại và sửa q17 thành câu hỏi về sinh đôi (đáp án 07 tháng).
  - Benchmark được chốt ở commit `e3698b3`, trước lần chạy truy xuất đầu tiên.
- **Tiêu chí chọn đã chốt trước khi xem test.**
  - Thứ tự xét: (1) Evidence Hit@3 trên dev; (2) Hit@1 trên dev; (3) ít chunk hơn / ingest nhanh hơn.
  - B và C hòa ở cả ba tiêu chí vì dùng chung một index. Mình chọn **C** dựa vào tiêu chí phụ **không có trong danh sách đăng ký trước**: filter tuân thủ 4/4 so với 3/4 trên dev. Quyết định ghi trong [results/strategy_selection.json](../../results/strategy_selection.json) trước khi chạy test.

## Kết quả

| Strategy | Split | Hit@1 (n/N) | Evidence Hit@3 (n/N) | Chunk count | Thời gian | Chi phí |
|---|---|---|---|---|---|---|
| A | dev | 5/16 | 7/16 | 492 | ingest 3,25 s; search 21 câu 0,17 s | 0 |
| A | test | 2/5 | 3/5 | 492 | | 0 |
| B | dev | 10/16 | 13/16 | 616 | ingest 3,21 s; search 21 câu 0,16 s | 0 |
| B | test | 4/5 | 5/5 | 616 | | 0 |
| C | dev | 10/16 | 13/16 | 616 (dùng chung với B) | search 21 câu 0,17 s | 0 |
| C | test | 4/5 | 5/5 | 616 | | 0 |

Ghi chú: thời gian embed query cho 21 câu là 0,16 s (dùng chung). Đo trên một máy macOS arm64, chạy 1 lần.

| Strategy | Coherence trung bình /2 (n) | Metadata đủ và đúng (n/N) | Filter tuân thủ (n/N) | Grounding 0/1/2 (số câu) và trung bình /2 |
|---|---|---|---|---|
| A | 0,00 (10) | 12/15 | dev 2/4 · test 1/2 | 1 / 0 / 4 · 1,6 |
| B | 1,60 (10) | 14/15 | dev 3/4 · test 1/2 | 0 / 0 / 5 · 2,0 |
| C | 1,60 (10; dùng chung chunk với B) | 15/15 | dev 4/4 · test 2/2 | 0 / 0 / 5 · 2,0 |

**Trace, nhãn và cách chọn mẫu.**
- Trace top-3: `results/traces/{A,B,C}_{dev,test,all}.jsonl`. Bảng tổng hợp: [results/summary.md](../../results/summary.md). Nhãn: `results/audit/*.csv`. Tổng hợp nhãn: [results/audit_summary.json](../../results/audit_summary.json).
- **Coherence:** 10 vị trí cố định trước khi xem điểm, gồm 10 trang cách đều nhau (1, 6, 11, …, 46), mỗi vị trí ở giữa text của trang; chấm chunk đầu tiên chứa vị trí đó.
- **Metadata và grounding:** 5 câu test × top-3 × 3 chiến lược.
- Cách quy đổi điểm coherence: 2 = chunk bắt đầu và kết thúc ở ranh giới câu/khoản; 1 = một đầu bị cắt giữa câu hoặc nhãn khoản bị tách; 0 = cả hai đầu bị cắt giữa câu/giữa từ.
- **Người chấm: AI (Claude Opus 5.5), chưa được học viên xác nhận** (cột `labeler`). AI đọc toàn văn từng chunk và gắn nhãn kèm lý do. Kiểm tra `article_ok` có script hỗ trợ: đếm số ký tự của mỗi điều trong chunk và so với metadata. Đây không phải nhãn thủ công độc lập; xem phần Hạn chế.

## Phân tích và kết luận

- **A–B: thay cách chunk.**
  - B tăng Evidence Hit@3 trên dev từ 7/16 lên 13/16 và Hit@1 từ 5/16 lên 10/16. Không có câu nào A trúng mà B trượt.
  - Nguyên nhân chính là A cắt ngang câu: 6/21 câu A lấy đúng trang gold nhưng evidence bị tách sang hai chunk. Coherence của A là 0/2 ở cả 10 vị trí, trong khi B được 6 lần 2 điểm và 4 lần 1 điểm. Bốn chunk B bị 1 điểm đều do khoản dài hơn 450 ký tự bị cắt cứng.
  - Đánh đổi: B có nhiều chunk hơn (616 so với 492) nhưng ít token hơn (49,3k so với 55,2k), vì không có overlap.
  - **Ví dụ sai 1, q18 (test), A:** top-3 đều là Điều 139 (nghỉ thai sản, trang 29). Điều 137 khoản 1 điểm a ("Mang thai từ tháng thứ 07…", trang 28) không có trong top-3 vì chunk chứa nó bị cắt giữa câu, nên grounding = 0. B đưa đúng Điều 137 lên top-1.
  - **Ví dụ sai 2, q14 (dev), B/C:** hỏi số ngày nghỉ Tết. Điều 112 (nghỉ lễ, tết) bắt đầu ở cuối trang 23, còn dòng "b) Tết Âm lịch: 05 ngày" nằm ở đầu trang 24. Vì chunk không bao giờ vượt qua ranh giới trang, điều này bị tách làm hai:
    - Chunk trang 23 có tên Điều 112 và vào top-3 ở hạng 3, nhưng không có đáp án.
    - Chunk trang 24 có đáp án nhưng mất tên điều, nên không vào top-3.

    Top-1 và top-2 lại là khoản 3 Điều 101 (tạm ứng khi nghỉ hằng năm) và Điều 114 (thâm niên). Đây là **mặt trái của quy tắc "một chunk = một trang"**: quy tắc này giúp truy vết trang chính xác nhưng làm vỡ những điều luật vắt qua hai trang.
  - Hai lỗi B do xếp hạng của model:
    - q01: "độ tuổi lao động tối thiểu" kéo về các đoạn tuổi nghỉ hưu ở trang 45.
    - q08: khoản 3 Điều 46/47 xếp trên khoản 1, là khoản chứa "một nửa tháng tiền lương".
- **B–C: thêm filter `chapter`.**
  - Trên benchmark này filter **không làm thay đổi Hit@1/Hit@3**, vì evidence gold của 6 câu filter vốn đã nằm trong top-3 của B ở cùng thứ hạng.
  - Tác dụng thấy được là loại chunk sai chương khỏi top-3: q16 (B có 1 chunk Chương XI) và q19 (B có Điều 105, Chương VII, "08 giờ/ngày", dễ khiến câu trả lời nhầm sang quy định cho người lớn). Nhờ vậy filter tuân thủ tăng từ 4/6 lên 6/6 và metadata đủ, đúng từ 14/15 lên 15/15.
  - Một trường có ích: `article` + `page` cho phép trích dẫn kiểu "Điều 146 khoản 1, trang 30" và đối chiếu ngay với PDF.
  - Một hạn chế: metadata lấy theo vị trí bắt đầu, nên chunk mở bằng heading chương bị trống `article` (q19 B top-3). Với A, chunk vắt qua hai điều có thể bị gắn sai điều (3/15 bản ghi).
- **Chiến lược chọn, đánh đổi và điều kiện áp dụng.**
  - **Chọn C**, tức chunk theo cấu trúc Điều/khoản + lọc theo chương khi người dùng nêu chương.
  - C chỉ có lợi khi câu hỏi cung cấp chương đúng. Nếu người dùng nêu sai chương, filter sẽ loại mất evidence; trường hợp này chưa được thử.
  - Với câu không có filter, C giống hệt B. Ví dụ q20 cố ý không có filter: B/C vẫn tìm đúng Điều 162 nhưng chỉ ở hạng 2, xếp sau một đoạn Chương V về thời gian thương lượng.
  - Kết luận chỉ áp dụng cho corpus này (một văn bản luật có cấu trúc rõ, tiếng Việt) và model này.
- **Hạn chế.**
  - **Nhãn audit do AI gắn**, theo yêu cầu của học viên vì thiếu thời gian. Học viên chưa duyệt từng nhãn, nên các số coherence, metadata và grounding có nguy cơ thiên lệch: AI cũng là bên đã soạn benchmark và code.
  - Benchmark do AI soạn nháp, học viên duyệt, nên câu hỏi có thể thiên về cách diễn đạt gần văn bản.
  - Test chỉ có 5 câu và dev 16 câu, nên một câu tương ứng 20% hoặc 6,25%. Chênh lệch B và C trên test không có ý nghĩa thống kê.
  - B và C hòa theo tiêu chí đã chốt; lựa chọn C dựa trên tiêu chí bổ sung.
  - Thời gian chỉ đo 1 lần trên 1 máy.
  - Metric tự động đòi chunk chứa **trọn** câu evidence, nên có thể đánh trượt câu vẫn trả lời được. Ví dụ q21 A: auto metric báo miss nhưng top-2 vẫn chứa "… là 01 năm"; nhãn grounding thủ công cho 2 điểm.
  - Corpus chưa cập nhật sửa đổi 2025.
- **Nếu làm lại hoặc mở rộng.**
  - Học viên tự chấm lại nhãn, hoặc nhờ người thứ hai chấm độc lập.
  - Thêm hybrid BM25 + dense: các câu sai như q14 có từ khóa chính xác như "Tết Âm lịch".
  - Thêm tên điều vào đầu chunk tiếp nối ở trang sau (ví dụ "Điều 112. Nghỉ lễ, tết (tiếp)") để sửa lỗi kiểu q14, trong khi vẫn giữ trang gốc.
  - Gắn metadata theo phần lớn nội dung chunk thay vì theo vị trí bắt đầu.
  - Nhận diện nhãn khoản `1b.`.
  - Thử model có giới hạn token dài hơn, chẳng hạn bge-m3 hoặc multilingual-e5.
  - Thêm câu hỏi nêu sai chương để đo rủi ro của filter, và tăng số câu test.

## Sử dụng AI

Claude Code (Claude Opus 5.5) đã được dùng cho:
- viết code pipeline;
- soạn nháp benchmark (học viên duyệt và sửa q17);
- chạy thí nghiệm;
- gắn nhãn audit;
- soạn báo cáo.

Cách kiểm tra:
- script xác minh evidence;
- assert `chunk.text == page.text[start:end]`;
- kiểm tra nạp lại và tài liệu thay đổi;
- học viên đọc lại benchmark, kết quả và báo cáo.
