# Blaze — Tài liệu API: Đăng ký / Đăng nhập / API Key / Usage / Rate Limit

> Nguồn: reverse engineer từ frontend `app.blaze.vn` (bundle `index-822fdb01.js` + chunk `index-c6ecb215.js`, `index-0ade8c53.js`, `index-a6b683fe.js`, `index-259f4fd2.js`).
> Đây là API **nội bộ** mà web client dùng — không phải docs chính thức. Có thể đổi bất cứ lúc nào.

---

## 0. Tổng quan

| Hạng mục | Giá trị |
|---|---|
| Base URL (auth/tokens/usage) | `https://gateway.blaze.vn` |
| Base URL (data plane, dùng API key) | `https://api.blaze.vn/v1` |
| Content-Type | `application/json` |
| Auth cơ chế | **Cookie phiên** (`withCredentials: true`) — không có Bearer token cho luồng dashboard |
| Response | Axios đã unwrap: body trả về **trực tiếp** (không bọc `data`) |

Frontend tạo instance:

```js
axios.create({
  baseURL: "https://gateway.blaze.vn",
  withCredentials: true,
  headers: { "Content-Type": "application/json" },
});
```

**Lưu ý:** mọi endpoint dưới đây đều bắt buộc cookie phiên. Trong `curl` phải giữ cookie bằng `-c/-b` hoặc `--cookie-jar`.

---

## 1. Đăng ký

### `POST /auth/sign_up`

**Body**

| Field | Type | Bắt buộc | Ràng buộc |
|---|---|---|---|
| `email` | string | ✅ | phải đúng định dạng email |
| `password` | string | ✅ | độ dài **8–32** ký tự |
| `isTermsAndConditionsAccepted` | boolean | ✅ | phải `true` |
| `isMarketingAccepted` | boolean | ✅ | `true`/`false` đều được, **không được null/undefined** |
| `language` | string | ✅ | `"vi"` \| `"en"` |
| `referralCode` | string | ⛔ | chỉ gửi khi có `?ref=CODE` trên URL |

> Form client còn có `repeatPassword`, nhưng field này **không** được gửi lên server — chỉ validate phía client.

**Ví dụ**

```bash
curl -i -X POST https://gateway.blaze.vn/auth/sign_up \
  -H 'Content-Type: application/json' \
  -c cookies.txt \
  -d '{
    "email": "ban@example.com",
    "password": "matkhau123",
    "isTermsAndConditionsAccepted": true,
    "isMarketingAccepted": false,
    "language": "vi"
  }'
```

**Trả về**

- Thành công: cookie phiên được set (xem `Set-Cookie`), body rỗng hoặc object user (client không đọc body — nó chỉ điều hướng sang `/signin?email=...`).
- Thất bại: HTTP 4xx, body dạng `{ "type": "<mã-lỗi>", "message": "..." }`.

**Mã lỗi `type` của sign_up**

| `type` | Ý nghĩa |
|---|---|
| `email-exists` | email đã tồn tại |
| `not-invited` | cần lời mời |
| `email-not-verified` | chưa xác thực email |
| `not-in-waitlist` | không nằm trong waitlist |

**Bước tiếp theo:** backend gửi mail xác thực. Frontend không expose endpoint verify (chỉ có modal hiển thị) — link verify nằm trong mail.

---

## 2. Đăng nhập

### `POST /auth/sign_in`

**Body**

| Field | Type | Bắt buộc |
|---|---|---|
| `email` | string | ✅ |
| `password` | string | ✅ |

```bash
curl -i -X POST https://gateway.blaze.vn/auth/sign_in \
  -H 'Content-Type: application/json' \
  -c cookies.txt \
  -d '{"email":"ban@example.com","password":"matkhau123"}'
```

**Trả về:** set cookie phiên → từ giờ mọi request kèm `-b cookies.txt`.

**Mã lỗi `type` của sign_in**

| `type` | Ý nghĩa |
|---|---|
| `invalid-password` | sai mật khẩu |
| `not-invited` | cần lời mời |
| `email-not-verified` | chưa xác thực email |
| `not-in-waitlist` | không nằm trong waitlist |

### `GET /auth/session`

Check phiên hiện tại. Không param.

- 200 → object user (client đọc `email` để quyết định `isLoggedIn`)
- 401 → chưa đăng nhập

### `POST /auth/sign_out`

Đăng xuất. Không body. Xóa cookie phiên.

### `POST /auth/google/login`

Login bằng Google OAuth (implicit flow).

| Field | Type | Bắt buộc |
|---|---|---|
| `token` | string | ✅ — `access_token` Google |
| `referralCode` | string | ⛔ |

---

## 3. Mật khẩu

### `POST /auth/forgot_password` — `POST`

Gửi mail đặt lại mật khẩu. Body: `{ "email": "..." }`.

### `PUT /auth/set_new_password`

Đặt mật khẩu mới từ link trong mail. Gọi tại route `/setNewPassword/:token`.

### `PUT /auth/change_password`

Đổi mật khẩu khi đã đăng nhập. Body gồm mật khẩu hiện tại + mật khẩu mới.

Mã lỗi thường gặp: `invalid-password`, `password-already-used`.

---

## 4. Quản lý API Key (token)

Route trên web: `/api/tokens`.

### `GET /tokens` — danh sách key

**Response:** mảng object

```json
[
  {
    "token": "sk_xxxxxxxxxxxx",
    "label": "prod-server",
    "createdAt": "2026-01-15T08:30:00Z",
    "lastUsedAt": "2026-06-01T12:00:00Z",
    "isDisabled": false
  }
]
```

| Field | Ghi chú |
|---|---|
| `token` | giá trị key đầy đủ — client copy thẳng field này |
| `label` | tên key, ≤32 ký tự |
| `createdAt` / `lastUsedAt` | ISO date; `lastUsedAt` có thể `null` |
| `isDisabled` | `false` = active, `true` = disabled |

### `POST /tokens` — tạo key

**Body**

| Field | Type | Bắt buộc | Ràng buộc |
|---|---|---|---|
| `label` | string | ✅ | bắt buộc, **tối đa 32 ký tự**, unique |

```bash
curl -X POST https://gateway.blaze.vn/tokens \
  -H 'Content-Type: application/json' \
  -b cookies.txt \
  -d '{"label":"my-key"}'
```

**Response:** object token mới (có `token`, `label`, `createdAt`, `isDisabled:false`).
**Lưu ý:** ghi lại `token` ngay — UI chỉ hiển thị để copy.

**Mã lỗi `type`**

| `type` | Ý nghĩa |
|---|---|
| `label-exists` | đã có key trùng `label` |
| `limit-exceeded` | chạm giới hạn số lượng key |
| `subscription-upgrade-required` | gói hiện tại chưa được tạo key (HTTP 401) |
| `not-found` | không tìm thấy |

### `PATCH /tokens` — bật/tắt key

**Body**

```json
{ "token": "sk_xxx", "isDisabled": true }
```

- `isDisabled: false` → activate
- `isDisabled: true` → disable

Lỗi: `not-found`.

### `DELETE /tokens` — xóa key

**Body**

```json
{ "token": "sk_xxx" }
```

> **Không** xác nhận bằng `label` — form modal có field `label` để người dùng gõ lại đúng, nhưng API chỉ nhận `token`.
> Lỗi: `not-found`.

---

## 5. Usage (số credit)

### `GET /usage`

Không param.

```json
{
  "appUsage": {
    "creditsAmountUsed": 120,
    "creditsAmountLimit": 500,
    "availableCredits": 380
  },
  "apiUsage": {
    "creditsAmountUsed": 45,
    "creditsAmountLimit": 200,
    "availableCredits": 155
  }
}
```

- `appUsage` = credit cho app; `apiUsage` = credit cho API.
- Số dư = `creditsAmountLimit - creditsAmountUsed`.
- Client còn đọc `appUsage.availableCredits` và `apiUsage.availableCredits`.

### `GET /usage/monthly_api_usage`

**Query params**

| Param | Type | Mặc định | Ghi chú |
|---|---|---|---|
| `page` | int | `1` | |
| `take` | int | `24` | |
| `groupBy` | string | `products` | `products` \| `tokens` |
| `authToken` | string | — | lọc theo 1 token cụ thể (chỉ gửi khi `groupBy=...` + chọn key) |

```bash
curl -b cookies.txt 'https://gateway.blaze.vn/usage/monthly_api_usage?page=1&take=24&groupBy=products'
```

**Response**

```json
{
  "data": [
    {
      "startedAt": "2026-06-01T00:00:00Z",
      "authToken": "sk_xxx",
      "tokenLabel": "prod-server",
      "productUsage": [
        {
          "product": "tts",
          "creditsAmountUsed": 12.5,
          "modelUsage": [
            { "model": "blaze-2.0-pro", "creditsAmountUsed": 8.2 },
            { "model": "blaze-2.0", "creditsAmountUsed": 4.3 }
          ]
        },
        {
          "product": "stt",
          "creditsAmountUsed": 3.1
        }
      ]
    }
  ]
}
```

- `modelUsage` **optional** — nếu không có thì cộng thẳng `creditsAmountUsed` theo `product`.
- Khi `groupBy=tokens`, giá trị chart lấy từ `tokenLabel || authToken.slice(0, 8) + "..."`, tổng `creditsAmountUsed` của mọi `productUsage`.

**Enum `groupBy`**

| Giá trị |
|---|
| `products` |
| `tokens` |

---

## 6. Rate limits

### `GET /usage/rate_limits`

Không param.

```json
{
  "rateLimits": [
    {
      "productType": "tts",
      "currentUsage": 12,
      "queriesPerPeriod": 100,
      "remaining": 88,
      "periodInSeconds": 600,
      "resetAt": 1791270000
    }
  ]
}
```

| Field | Kiểu | Ghi chú |
|---|---|---|
| `productType` | string | enum dưới đây |
| `currentUsage` | number | đã dùng trong chu kỳ |
| `queriesPerPeriod` | number | hạn mức chu kỳ |
| `remaining` | number | còn lại |
| `periodInSeconds` | number | độ dài chu kỳ (giây) |
| `resetAt` | number | **unix timestamp (giây)** — client lấy `resetAt - Date.now()/1000` để đếm ngược |

**`productType` hợp lệ**

```
tts, stt, search, summarization, translation, radio,
liveInterpreter, system, speakerEmbedding, voiceProfiler,
voiceSeparation, antiSpoofing
```

**Ngưỡng hiển thị** (client tự set): `usage/queriesPerPeriod >= 0.9` → `exception`, `>= 0.7` → `normal`, còn lại → `success`.

### Bảng tier (client hard-code, không phải từ API)

| Chi tiêu tối thiểu (USD) | Concurrent | Req / 10 phút | Req / giờ |
|---|---|---|---|
| $0+ | 20 | 100 | 600 |
| $500+ | 50 | 250 | 1.500 |
| $1.000+ | 100 | 500 | 3.000 |
| $2.000+ | 200 | 1.000 | 6.000 |
| $6.000+ | 600 | 3.000 | 18.000 |

Tier hiện tại = tier có `requestsPer10Min` khớp giá trị `max(queriesPerPeriod)` trả về từ `/usage/rate_limits`.

---

## 7. Bảng mã lỗi chung (`type`)

Dùng cho `sign_in`, `sign_up`, `forgot_password`, `change_password`:

| `type` | Ý nghĩa |
|---|---|
| `email-exists` | email đã tồn tại |
| `email-not-verified` | chưa xác thực email |
| `invalid-password` | sai mật khẩu |
| `password-already-used` | mật khẩu mới trùng mật khẩu cũ |
| `not-invited` | cần lời mời |
| `not-in-waitlist` | không trong waitlist |
| `not-found` | không tìm thấy |
| `already-requested` | đã yêu cầu trước đó |
| `expired` | token/link hết hạn |

Dùng cho `/tokens`:

| `type` |
|---|
| `limit-exceeded` |
| `not-found` |
| `label-exists` |
| `subscription-upgrade-required` |

---

## 8. Ví dụ end-to-end

```bash
# 1. đăng nhập (giữ cookie)
curl -s -c blaze.jar -X POST https://gateway.blaze.vn/auth/sign_in \
  -H 'Content-Type: application/json' \
  -d '{"email":"ban@example.com","password":"matkhau123"}'

# 2. tạo API key
curl -s -b blaze.jar -X POST https://gateway.blaze.vn/tokens \
  -H 'Content-Type: application/json' \
  -d '{"label":"my-project"}'
# -> lấy .token trong response

# 3. xem credit
curl -s -b blaze.jar https://gateway.blaze.vn/usage

# 4. xem rate limit
curl -s -b blaze.jar https://gateway.blaze.vn/usage/rate_limits

# 5. dùng API key ở data plane — truyền qua header Authorization: Bearer
curl -s -X POST https://api.blaze.vn/v1/tts \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <TOKEN>' \
  -d '{
    "query": "Xin chào các bạn",
    "speaker_id": "HN-Nam-1-BL",
    "language": "vi",
    "model": "v2.0_pro",
    "audio_format": "mp3",
    "audio_quality": 4,
    "audio_speed": "1.00",
    "normalization": "basic"
  }'
# -> {"id":"<job_id>","status":"processing"}

# 6. dò trạng thái job rồi tải audio
curl -s -H 'Authorization: Bearer <TOKEN>' \
  https://api.blaze.vn/v1/tts/<job_id>/info
# -> {"status":"processing"|"completed"|"failed"|"error", ...}

curl -s -o out.mp3 -H 'Authorization: Bearer <TOKEN>' \
  https://api.blaze.vn/v1/tts/<job_id>/download

# 7. kho giọng (dùng token, KHÔNG phải cookie phiên)
curl -s -H 'Authorization: Bearer <TOKEN>' https://gateway.blaze.vn/tts/options
# -> {"speakers":[{"id","name","language","gender","province","type"}]}
```

### **Đã đối chiếu** — cách truyền key ở data plane

Trước đây mục này để placeholder. Nay chốt được, nguồn là client TTS Blaze **đang chạy thật**
trong repo này (`reviewtrans/core/providers/tts/blaze.py`, hàm `_request`):

```python
url = f"{base or self.base}{path}"          # base = https://api.blaze.vn
headers = {"Authorization": f"Bearer {state.token}", "Accept": "application/json"}
if json_body is not None:
    headers["Content-Type"] = "application/json"
requests.request(method, url, json=json_body, params=params, headers=headers)
```

- **Header `Authorization: Bearer <token>`** — không truyền qua query param, không dùng cookie.
- Endpoint management (`/auth/*`, `/tokens`, `/usage/*`) trên `gateway.blaze.vn` mới dùng **cookie phiên**.
- Riêng `GET https://gateway.blaze.vn/tts/options` (kho giọng) lại dùng **Bearer token** —
  hai cơ chế nằm chung một host, đừng nhầm.
- `401` / `403` → token bị từ chối · `429` → token vừa vượt hạn mức cửa sổ.

**Enum đã xác nhận** (đọc từ client):

| Trường | Giá trị hợp lệ |
|---|---|
| `model` | `v2.0_pro` · `v2.0_flash` · `v1.5_pro` · `v1.5_flash` |
| `audio_format` | `mp3` · `wav` · `opus` |
| `language` | `vi` · `en` |
| `normalization` | `no` · `basic` · `advanced` |
| `audio_quality` | số nguyên |
| `audio_speed` | chuỗi 2 chữ số thập phân, kẹp `[0.50 … 2.00]` |
| `speaker_id` | mặc định `HN-Nam-1-BL`, lấy từ `/tts/options` |

**Hạn mức data plane** (per token, không phải rate limit ở mục 6 — hai thứ khác nhau):

| Cửa sổ | Giới hạn |
|---|---|
| 10 phút | 100 request |
| 1 giờ | 600 request |
| Đồng thời | 20 job |

> Tốn 3 request cho 1 lượt đọc (`POST /v1/tts` → `GET …/info` poll → `GET …/download`),
> nên 100/10 phút ≈ 33 đoạn đọc cho mỗi token.

---

## 9. Endpoint phụ trợ (tham khảo)

```
GET    /user                      thông tin user
GET    /user/language             ngôn ngữ hiện tại
PATCH  /user/language             đổi ngôn ngữ        body: { language }

GET    /usage/purchase_credits    (POST) mua credit
GET    /usage/credit_transactions lịch sử giao dịch credit
GET    /billing                   thông tin thanh toán
GET    /billing/plan              gói hiện tại
PATCH  /billing/plan              đổi gói
GET    /billing/plans/info        danh sách gói
GET    /billing/spending_limit    hạn chi tiêu
PUT    /billing/spending_limit    đặt hạn chi tiêu     body: { ... }
GET    /tts/options               tùy chọn TTS (speaker, model...)
GET    /voicebots                 danh sách voicebot
```

---

## 10. Rủi ro / lưu ý

- Cookie phiên = giá trị đăng nhập. Không chia sẻ `cookies.txt`.
- Endpoint đổi bất cứ lúc nào, không có versioning ở tầng `gateway.blaze.vn`.
- `email-not-verified` / `not-in-waitlist` cho thấy backend có chặn đăng nhập khi chưa xác thực hoặc chưa nằm trong danh sách chờ — đừng đoán `type` từ status code, đọc body.
- Rate limit tier bảng ở mục 6 hard-code trong frontend, có thể lệch với server.
