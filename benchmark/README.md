# Benchmark — Bộ luật Lao động 2019 (45/2019/QH14)

File: [questions.jsonl](questions.jsonl) — 21 câu, **16 dev / 5 test**, chốt trước mọi lần chạy truy xuất.

## Các trường

`id`, `split`, `type` (direct / paraphrase / conditional / metadata), `question`, `expected_answer`,
`gold_source_id` (= `source_id` trong manifest, `blld-2019`), `gold_page` (danh sách số trang vật lý của file PDF),
`evidence_quote` (trích nguyên văn từ text trang đã chuẩn hóa), `filter` (`{"chapter": n}` hoặc `null`).
Trường tùy chọn `alternatives` (nguồn/trang/trích dẫn hợp lệ khác): không cần dùng vì mỗi evidence chỉ xuất hiện ở đúng 1 trang.

## Chia dev/test và kiểm soát rò rỉ

- **Test chỉ lấy từ các chương không có câu dev nào**: Chương X (lao động nữ), XI (lao động chưa thành niên,
  giúp việc gia đình) và XIV (tranh chấp lao động).
- Dev lấy từ các Chương I, III, VI, VII, VIII, XII.
- Nguy cơ rò rỉ còn lại: một số khái niệm lặp lại giữa các chương (ví dụ "báo trước" có ở Chương III và XI,
  "làm thêm giờ", "ban đêm" có ở Chương VI, VII và X), nên dev và test không hoàn toàn độc lập về chủ đề.
- Phân bố loại câu: direct 7, paraphrase 2, conditional 6, metadata/filter 6.

| id | split | Chương | Điều | trang |
|---|---|---|---|---|
| q01–q02 | dev | I | 3 | 1, 2 |
| q03–q08 | dev | III | 20, 25, 26, 35, 46 | 5–11 |
| q09–q10 | dev | VI | 98 | 21 |
| q11, q13–q15 | dev | VII | 105, 107, 112, 115 | 22–24 |
| q16 | dev | VIII | 127 | 27 |
| q12 | dev | XII | 169 | 34 |
| q17–q18 | test | X | 139, 137 | 29, 28 |
| q19–q20 | test | XI | 146, 162 | 30, 33 |
| q21 | test | XIV | 190 | 39 |

## Câu hỏi kiểm tra metadata filter

6 câu (dev: q07, q09, q13, q16; test: q18, q19). Giá trị filter được nêu ngay trong câu hỏi ("Theo Chương N…"),
tức là ngữ cảnh người dùng cung cấp trước khi truy xuất, không lấy từ đáp án. Chọn những câu mà khái niệm cũng xuất hiện ở
chương khác, để filter có ý nghĩa:

| id | khái niệm | chương gold | khái niệm này còn xuất hiện ở chương |
|---|---|---|---|
| q07 | báo trước khi đơn phương chấm dứt | III | VII, X, XI, XVI |
| q09 | tiền lương làm thêm giờ | VI | III, V, VII, X, XI |
| q13 | giới hạn giờ làm thêm | VII | III, V, VI, X, XI |
| q16 | hành vi bị nghiêm cấm | VIII | I (Điều 8) |
| q18 | làm thêm giờ, ban đêm khi mang thai | X | VI, VII, XI |
| q19 | thời giờ làm việc | XI | III, VI, VII, VIII, X, XIII |

Cấu hình C chỉ áp dụng filter cho các câu này; với các câu khác, C chạy không filter (giống hệt B).
Ngoài ra q20 ("báo trước" với người giúp việc, Chương XI) **cố ý không có filter** để quan sát nhầm lẫn với Điều 35 (Chương III).

## Kiểm chứng evidence

`python -m src.run verify-benchmark` kiểm tra mỗi `evidence_quote` nằm trong text đã chuẩn hóa của trang gold
(so khớp sau chuẩn hóa: NFKC, đổi ngoặc kép/gạch ngang về dạng thường, không phân biệt hoa thường, gộp khoảng trắng).
Kết quả: [../results/benchmark_verification.json](../results/benchmark_verification.json). Một bước kiểm tra phụ xác nhận
mỗi evidence chỉ xuất hiện ở đúng 1 trang, và với câu có filter thì evidence thuộc đúng chương ghi trong filter.
Evidence luôn nằm trong 1 trang vì chunk không bao giờ vượt qua ranh giới trang.

## Tiêu chí chọn chiến lược (chốt trước khi chạy test)

1. **Evidence Hit@3 trên dev** cao nhất.
2. Nếu bằng nhau → **Hit@1 trên dev** cao nhất.
3. Nếu vẫn bằng nhau → ít chunk hơn / thời gian ingest thấp hơn.

Kết quả test được báo cáo cho cả ba chiến lược sau khi đã chốt lựa chọn; không dùng test để tinh chỉnh.

## Ghi chú tác giả

Câu hỏi, đáp án và evidence được soạn nháp với sự hỗ trợ của AI (Claude) từ text đã chuẩn hóa, số trang do script
tự định vị; học viên đã đọc, đối chiếu với PDF và duyệt trước khi chốt (xem lịch sử git: commit chốt benchmark có trước
lần chạy truy xuất đầu tiên).
