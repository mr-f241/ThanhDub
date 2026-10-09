# BÁO CÁO LỖ HỔNG — CỔNG CALLBACK NẠP TIỀN

**Mục tiêu:** `sngocrong.com` (origin `38.54.15.160`)
**Endpoint:** `POST/GET /recharge/callback/{callback_slug}`
**Mức độ dự kiến:** Cao → Nguy hiểm (nếu xác nhận)
**Trạng thái:** ⚠️ **CHƯA XÁC NHẬN** — xem mục 1.2

---

## 1. TÓM TẮT

### 1.1 Kết luận ở mức đã quan sát

Cổng callback nạp tiền phản hồi **HTTP 200 với body rỗng cho mọi giá trị slug**, kể cả slug không tồn tại:

| Slug gửi lên | HTTP | Body |
|---|---|---|
| `test` | 200 | `0 byte` |
| `default` | 200 | `0 byte` |
| `admin` | 200 | `0 byte` |
| `1` | 200 | `0 byte` |
| `12345` | 200 | `0 byte` |
| `callback` | 200 | `0 byte` |

Không phân biệt slug hợp lệ / không tồn tại / slug tĩnh đoán được.

### 1.2 ⚠️ Những gì CHƯA được kiểm chứng

Báo cáo này **chưa xác nhận** lỗ hổng nạp tiền free có tồn tại hay không. Các mục sau **chưa được thử**, vì chúng nằm trên hệ thống đang chạy và có khả năng ghi dữ liệu tài chính:

- [ ] `POST` tới callback có được xử lý hay bị chặn bởi xác thực chữ ký
- [ ] Callback có ghi tín dụng khi **không** kèm chữ ký hợp lệ
- [ ] Số tiền lấy từ payload callback hay được đối chiếu lại với order phía server
- [ ] Replay (gửi lại cùng 1 giao dịch) có bị chặn
- [ ] Rate limit có hoạt động trên endpoint này không

**Mục 5 là kế hoạch xác minh để đội vận hành tự chạy.** Không nên vá盲 theo phỏng đoán — nhưng các fix ở mục 6 là biện pháp phòng vệ chuẩn, vá trước cũng không hại gì.

### 1.3 Bằng chứng cấu hình (đã lấy từ bundle build)

Form cấu hình nạp tiền — mỗi "server" là một dòng cấu hình riêng, gồm:

| Field | Vai trò | Rủi ro |
|---|---|---|
| `callback_slug` | Định danh endpoint callback, admin tự gõ text | Đoán được, không random |
| `partner_id` | Mã đối tác | Định danh |
| `partner_key` | Khóa đối tác (`type:password`, có "để trống để giữ nguyên") | Bí mật |
| `host` / `port` / `username` / `password` / `unix_socket` | **Kết nối DB game trực tiếp** | Table admin chứa credential DB |
| `use_remote_db` | Bật kết nối DB từ xa | Tăng bề mặt tấn công |
| `is_active` | Bật/tắt | — |
| `web_login_enabled` / `web_registration_enabled` | Cho login/register từ web | — |

URL callback hiển thị ngay trong form:
```
Callback: /recharge/callback/{callback_slug}
```

**Nhận xét cấu hình:** `callback_slug` là **text input tự do**, không có ghi nhận nào là được sinh ngẫu nhiên. Nếu admin đặt slug dễ đoán (`test`, `nap`, `callback`, `1`, tên kênh) thì lớp định danh duy nhất của cổng callback trở thành một chuỗi đoán được.

---

## 2. BỐI CẢNH KỸ THUẬT

### 2.1 Luồng nạp tiền chuẩn

```
[Người nạp] → [Cổng thanh toán / nhà cung cấp]
                    │
                    │  1. Xác nhận đã thu tiền
                    ▼
        POST /recharge/callback/{slug}
        body: { giao_dich_id, so_tien, trang_thai, ... }
                    │
                    ▼
        [Server game] cộng tiền vào tài khoản
```

**Điểm mấu chốt:** server game **không nhìn thấy** giao dịch thật. Nó chỉ nhận một HTTP request do một bên thứ ba gửi tới. Nếu không kiểm tra request đó đến từ đâu và có đúng ý nghĩa hay không, thì **bất kỳ ai cũng giả lập được nhà cung cấp**.

### 2.2 Ba lớp phòng vệ bắt buộc

| Lớp | Cơ chế | Nếu thiếu |
|---|---|---|
| **A. Nguồn gốc** | Danh sách IP nhà cung cấp + TLS/mTLS | Ai cũng gọi được endpoint |
| **B. Chữ ký** | HMAC-SHA256 trên payload, so sánh `hash_equals` | Ai cũng tạo được payload hợp lệ |
| **C. Ngữ nghĩa** | Đối chiếu `so_tien`, `trang_thai`, order đang `pending`; chống replay | Tiền vào sai, vào nhiều lần |

Nếu cả 3 lớp đều vắng → đây chính là **"nạp tiền free"**. Báo cáo này chưa xác nhận lớp nào đang thiếu, nhưng **mọi triệu chứng ở mục 1.1 đều tương thích với giả thuyết endpoint đang mở**.

---

## 3. KỊCH BẢN BỊ LỢI DỤNG *(nếu xác nhận thiếu lớp A/B/C)*

> Đây là phần tiêu chuẩn của mọi báo cáo lỗ hổng — mô tả **cơ chế** để đội dev hiểu mức độ nghiêm trọng và xây đúng chốt chặn. Không kèm exploit đang chạy.

### Kịch bản 1 — Vẽ payload giả (thiếu lớp B)

- **Điều kiện:** endpoint chấp nhận `POST` mà không xác minh chữ ký, hoặc chữ ký so trên trường có thể tự chọn.
- **Cách lợi dụng:** đối tượng gửi thẳng HTTP request tới `/recharge/callback/{slug}` với trường `trang_thai=success` và `so_tien` theo ý muốn, bỏ qua hoàn toàn khâu thu tiền thật.
- **Kết quả:** số dư tăng mà không có giao dịch tương ứng.
- **Chốt chặn:** bắt buộc chữ ký HMAC hợp lệ trước khi đọc bất kỳ trường nào; dùng `hash_equals()` (so sánh hằng thời gian), **không** dùng `==`.

### Kịch bản 2 — Đoán slug (thiếu lớp A, kết hợp cấu hình)

- **Điều kiện:** slug do admin đặt dạng text dễ đoán, và IP nguồn không bị giới hạn.
- **Cách lợi dụng:** dò `/recharge/callback/` với danh sách slug phổ biến. Thực tế đã thấy **mọi slug đều trả 200**, nên dò không phân biệt được slug nào "thật" — nghĩa là bước này không còn là rào cản.
- **Kết quả:** thu hẹp được target, chuyển sang kịch bản 1.
- **Chốt chặn:** slug sinh ngẫu nhiên 32+ ký tự (`Str::random(40)`), **không** cho admin gõ tay; sai slug → `404`, không trả `200` rỗng (xem mục 6.1).

### Kịch bản 3 — Replay giao dịch thật (thiếu lớp C)

- **Điều kiện:** không có idempotency key / không khoá transaction duy nhất.
- **Cách lợi dụng:** một giao dịch nạp **hợp lệ, có tiền thật** bị gửi lại nhiều lần. Ví dụ capture được 1 request callback hợp lệ (log, proxy, MITM trên HTTP — lưu ý mục 4) rồi phát lại.
- **Kết quả:** một lần nạp → nhiều lần cộng tiền. Lỗ hổng này **im lặng**, khó phát hiện vì mỗi lần đều trông như giao dịch thật.
- **Chốt chặn:** unique index ở DB trên `(provider, transaction_id)` + `SELECT ... FOR UPDATE` trong transaction. Xem mục 6.3.

### Kịch bản 4 — Tăng số tiền (thiếu lớp C)

- **Điều kiện:** `so_tien` lấy trực tiếp từ payload mà không đối chiếu order.
- **Cách lợi dụng:** nạp hợp lệ số tiền nhỏ, nhưng payload báo số tiền lớn (nếu provider cho phép trường này tự do) — hoặc sửa order phía client.
- **Kết quả:** trả 10.000đ nhận 1.000.000đ.
- **Chốt chặn:** **không tin payload**. Lấy số tiền từ record order trong DB của mình; nếu cần, gọi API tra cứu giao dịch phía provider để đối chiếu độc lập. Xem mục 6.4.

### Kịch bản 5 — Đổi đích callback (kết hợp lỗ hổng Host header ở báo cáo trước)

- **Điều kiện:** origin nhận Host header tùy ý (đã xác nhận trong báo cáo trước: `Host: random-host.test` → 200), và provider cho cấu hình URL callback qua tham số có thể ảnh hưởng bởi URL tuyệt đối.
- **Cách lợi dụng:** nếu có bất kỳ chỗ nào server sinh URL tuyệt đối (`APP_URL`, `url()->current()`, `route()` absolute) từ Host, thì link callback / link reset mật khẩu có thể trỏ sang máy của đối tượng.
- **Kết quả:** bắt được request callback thật → chuyển sang kịch bản 3.
- **Chốt chặn:** set `APP_URL` cố định trong `.env`, sửa `server_name` nginx (đã nêu ở báo cáo trước).

---

## 4. YẾU TỐ TĂNG MỨC NGUY HIỂM ĐÃ XÁC NHẬN

Những điểm dưới đây **đã xác minh** ở đợt quét trước và làm kịch bản trên trầm trọng thêm:

1. **Origin IP lộ hoàn toàn** — `*.sngocrong.com` wildcard → `38.54.15.160`, bypass Cloudflare.
   → Callback có thể bị gọi **trực tiếp vào origin**, bỏ qua WAF, bot protection, rate limit của Cloudflare.
2. **Cổng 80 phục vụ app, cookie thiếu `secure`** — `sieungocrongvip_session` không có cờ `secure`.
   → Nếu callback hoặc bất kỳ luồng nào chạy qua HTTP plaintext → **bắt được payload thật** → kịch bản 3.
3. **Rate limit chưa đo được** — test login trả 419 (CSRF chặn trước).
   → Chưa rõ callback có bị giới hạn tần suất hay không. Cần kiểm ở mục 5.
4. **Không có CAA record** — mọi CA đều cấp được cert cho domain.
   → Tăng khả năng MITM để bắt callback thật.
5. **`build/manifest.json` public** — lộ toàn bộ route admin.

---

## 5. KẾ HOẠCH XÁC MINH *(chạy trên môi trường của mình / staging)*

> **Không chạy trên hệ thống production của bên thứ ba nếu bạn không sở hữu.** Đây là checklist để đội vận hành tự kiểm tra hệ thống của họ.

### Bước 1 — Method & auth
```bash
# 1. OPTIONS/POST không kèm chữ ký
curl -i -X POST "https://<domain>/recharge/callback/<slug_that_hop_le>" \
  -H "Content-Type: application/json" \
  -d '{}'
# KỲ VỌNG AN TOÀN: 401 / 403 / 404 / 422
# BÁO ĐỘNG:      200 → endpoint xử lý mà không kiểm tra gì
```

### Bước 2 — Slug không tồn tại
```bash
curl -i "https://<domain>/recharge/callback/slug_khong_ton_tai_12345"
# KỲ VỌNG AN TOÀN: 404
# BÁO ĐỘNG:      200 (đã quan sát — cần giải thích lý do)
```

### Bước 3 — Chữ ký sai nhưng có đủ trường
```bash
curl -i -X POST "https://<domain>/recharge/callback/<slug>" \
  -H "Content-Type: application/json" \
  -H "X-Signature: 0000" \
  -d '{"transaction_id":"TEST-VERIFY-001","amount":999999999,"status":"success"}'
# KỲ VỌNG AN TOÀN: 403
# BÁO ĐỘNG:      200 → chữ ký không được kiểm tra
```
→ **Sau test phải đối chiếu DB xem số dư có thay đổi không.** Đây là bước quyết định.

### Bước 4 — Replay
```bash
# Gửi lại 2 lần cùng transaction_id hợp lệ (dùng giao dịch test trên staging)
# KỲ VỌNG AN TOÀN: lần 2 bị bỏ qua (idempotent)
# BÁO ĐỘNG:      lần 2 vẫn cộng tiền
```

### Bước 5 — Rate limit
```bash
for i in $(seq 1 60); do
  curl -o /dev/null -w "%{http_code} " -X POST \
    "https://<domain>/recharge/callback/x" -d '{}'
done
# KỲ VỌNG AN TOÀN: xuất hiện 429
# BÁO ĐỘNG:      toàn bộ 200/419 → không giới hạn tần suất
```

### Bước 6 — Tra log
```bash
grep -E "recharge|callback" storage/logs/laravel.log | tail -50
# Kiểm tra: request không hợp lệ có bị ghi + cảnh báo không
```

**Ghi lại mã HTTP + trạng thái DB sau mỗi bước.** Kết quả đó quyết định mứcseverity thực sự.

---

## 6. HƯỚNG DẪN FIX

### 6.1 Không trả 200 mù — tra đúng slug rồi mới xử lý

```php
// app/Http/Controllers/RechargeCallbackController.php

public function __invoke(Request $request, string $slug)
{
    $server = DB::table('servers')
        ->where('callback_slug', $slug)
        ->where('is_active', 1)
        ->first();

    // slug không tồn tại → 404. KHÔNG trả 200 rỗng.
    // Trả 200 rỗng làm bên thứ ba không phân biệt được slug nào thật,
    // nhưng đồng thời cũng che mất tín hiệu đang bị dò.
    abort_unless($server, 404);

    // --- LỚP A: nguồn gốc ---
    if (! $this->ipAllowed($request->ip(), $server->provider_ips)) {
        logger()->warning('recharge.callback.ip_blocked', [
            'ip' => $request->ip(), 'slug' => $slug,
        ]);
        abort(403);
    }

    // --- LỚP B: chữ ký ---
    if (! $this->signatureValid($request, $server)) {
        logger()->warning('recharge.callback.bad_signature', [
            'ip' => $request->ip(), 'slug' => $slug,
        ]);
        abort(403);
    }

    return $this->handlePayment($request, $server);
}
```

### 6.2 Xác minh chữ ký HMAC

```php
private function signatureValid(Request $request, object $server): bool
{
    $given = $request->header('X-Signature', '');

    if ($given === '' || $server->webhook_secret === null) {
        return false;
    }

    // Payload gốc phải là đúng bytes đã nhận — KHÔNG tái dựng JSON
    // (re-encode làm thay đổi thứ tự key → hash lệch, hoặc tệ hơn: bỏ sót trường).
    $payload = $request->getContent();

    $expected = hash_hmac('sha256', $payload, $server->webhook_secret);

    // so sánh hằng thời gian — tránh timing attack
    return hash_equals($expected, $given);
}
```

**Lưu ý quan trọng:**
- `webhook_secret` phải là chuỗi ngẫu nhiên ≥ 32 byte, **sinh một lần, không cho gõ tay**.
- Không đặt secret trong frontend / bundle build — kiểm tra lại bằng:
  ```bash
  grep -riE "webhook_secret|partner_key" public/build/
  ```
- Nếu provider có hỗ trợ IP range công bố → cache danh sách đó, cập nhật định kỳ.

### 6.3 Chống replay — idempotency

```php
private function handlePayment(Request $request, object $server)
{
    $txId = $request->input('transaction_id');
    abort_unless($txId, 422);

    return DB::transaction(function () use ($request, $server, $txId) {
        // Khóa dòng — 2 request trùng chạy tuần tự, cái sau thấy đã xử lý
        $exists = DB::table('recharge_transactions')
            ->where('server_id', $server->id)
            ->where('provider_tx_id', $txId)
            ->lockForUpdate()
            ->first();

        if ($exists) {
            logger()->info('recharge.callback.replay_ignored', ['tx' => $txId]);
            return response()->json(['ok' => true, 'duplicate' => true]);
        }

        DB::table('recharge_transactions')->insert([
            'server_id'     => $server->id,
            'provider_tx_id' => $txId,
            'status'        => 'credited',
            'created_at'    => now(),
        ]);

        $this->creditWallet(/* ... */);

        return response()->json(['ok' => true]);
    });
}
```

**Bắt buộc kèm migration — unique index là chốt chặn cuối cùng, không dựa vào code một mình:**

```php
Schema::table('recharge_transactions', function (Blueprint $table) {
    $table->unique(['server_id', 'provider_tx_id'], 'uniq_tx_per_server');
});
```

> Without unique index, race condition vẫn cho phép 2 request trùng vào cùng lúc. Code `lockForUpdate()` phòng trước, index phòng sau.

### 6.4 Không tin số tiền từ payload

```php
// SAI — tin payload
$amount = $request->input('amount');
$user->credit($amount);

// ĐÚNG — lấy từ order trong DB của mình
$order = DB::table('recharge_orders')
    ->where('server_id', $server->id)
    ->where('provider_tx_id', $txId)
    ->lockForUpdate()
    ->first();

abort_unless($order, 404);
abort_unless($order->status === 'pending', 409);   // đã xử lý → từ chối
abort_unless($order->expected_amount == $request->input('amount'), 409);

// Nếu provider hỗ trợ: gọi API tra cứu giao dịch để đối chiếu độc lập
// trước khi cộng tiền. Không dựa 100% vào nội dung callback.
```

### 6.5 Rate limit

```php
// routes/web.php  (hoặc routes/api.php nếu tách)
Route::post('/recharge/callback/{slug}', RechargeCallbackController::class)
    ->middleware('throttle:recharge-callback,60,1');
```

```php
// AppServiceProvider::boot() — nới cho provider gọi thật,
// nhưng vẫn chặn dò slug / flood
RateLimiter::for('recharge-callback', function (Request $request) {
    return Limit::perMinute(60)->by($request->ip());
});
```

### 6.6 Slug ngẫu nhiên, không cho gõ tay

```php
// khi tạo server
$server->callback_slug = Str::random(40);

// form admin: bỏ input text, chỉ hiển thị slug đã sinh
```

### 6.7 Đóng các yếu tố cộng hưởng (liên quan báo cáo trước)

```nginx
# 1. Chặn host lạ  → chặn kịch bản 5
server {
    server_name sngocrong.com www.sngocrong.com;
    if ($host !~* "^(www\.)?sngocrong\.com$") { return 444; }
    ...
}

# 2. Redirect 80 → 443 + cookie secure  → chặn bắt payload qua HTTP
location / {
    return 301 https://$host$request_uri;
}
```

```php
// config/session.php
'secure'   => true,
'httponly' => true,
'same_site' => 'lax',
```

```
# 3. Bỏ wildcard DNS  → đóng origin
#    Xóa record *.sngocrong.com, bật proxy cho www riêng
```

### 6.8 Ghi log & cảnh báo

```php
logger()->warning('recharge.callback.rejected', [
    'slug'   => $slug,
    'ip'     => $request->ip(),
    'reason' => 'signature_mismatch',
]);
// + alert khi số lượng warning vượt ngưỡng → phát hiện đang bị dò
```

---

## 7. KIỂM TRA LẠI SAU KHI VÁ

- [ ] Slug không tồn tại → **404** (không còn 200 rỗng)
- [ ] `POST` không chữ ký → **403**, và **DB không đổi**
- [ ] Chữ ký sai → **403**
- [ ] Replay cùng `transaction_id` → lần 2 bị bỏ qua
- [ ] Số tiền payload ≠ số tiền order → **409**, không cộng tiền
- [ ] 60 request liên tiếp → xuất hiện **429**
- [ ] `grep webhook_secret public/build/` → **0 kết quả**
- [ ] Host lạ → **444**; `http://` → **301** về https
- [ ] Cookie có cờ `secure`
- [ ] Không còn record DNS wildcard `*.sngocrong.com`
- [ ] Callback chỉ chấp nhận IP trong danh sách provider
- [ ] Alert hoạt động khi có request bị từ chối

---

## 8. ƯU TIẾN

| # | Hạng mức | Việc cần làm |
|---|---|---|
| 1 | 🔴 Cao | Xác minh mục 5 — đây là **điều kiện tiên quyết** để biết mứcseverity thật |
| 2 | 🔴 Cao | Thêm verify chữ ký (6.2) + idempotency (6.3) |
| 3 | 🟠 Trung bình-cao | Không tin số tiền payload (6.4) |
| 4 | 🟠 Trung bình-cao | Đóng wildcard DNS + Host header (6.7) — đã nêu ở báo cáo trước |
| 5 | 🟡 Trung bình | Rate limit (6.5), slug ngẫu nhiên (6.6), log (6.8) |
| 6 | 🟡 Trung bình | Chặn `manifest.json` public |

---

**Lưu ý phạm vi:** Báo cáo dựa trên quét thụ động + đọc bundle build công khai. **Chưa thực hiện bất kỳ request nào có khả năng ghi dữ liệu tài chính.** Các kịch bản ở mục 3 là mô tả cơ chế để phục vụ sửa lỗi, chưa được xác nhận tồn tại trên hệ thống.
