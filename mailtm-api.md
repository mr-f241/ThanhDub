# Mail.tm — Temp Mail API

> Nguồn: docs chính thức `https://docs.mail.tm` + OpenAPI spec `https://api.mail.tm/docs.jsonld`.
> **Đã smoke test thật** ngày 2026-10-06: tạo account → lấy token → `GET /me` → `GET /messages` đều 200.

---

## 0. Tổng quan

| Hạng mục | Giá trị |
|---|---|
| Base URL | `https://api.mail.tm` |
| Auth | **Bearer JWT** từ `POST /token`. Không có API key, không cần đăng ký trước |
| Rate limit | **8 QPS / IP** (vượt → `429`) |
| Phí | Miễn phí hoàn toàn |
| Pagination | 30 item / trang, lấy từ `hydra:totalItems` |

**Điều khoản của họ (ghi lại cho đúng):**

- Không dùng cho việc bất hợp pháp
- Không build sản phẩm trả phí bọc lại API của họ
- Không mirror/proxy API dưới domain khác
- **Bắt buộc attribution** — nếu dùng API phải link về mail.tm ở chỗ dễ thấy

---

## 1. Content negotiation — chỗ dễ ăn hành nhất

Server trả `Content-Type` theo `Accept` gửi lên:

| `Accept` | Collection trả về |
|---|---|
| `application/ld+json` *(mặc định)* | object bọc hydra: `{ "@id", "hydra:totalItems", "hydra:member": [...] }` |
| `application/json` | **mảng thuần** `[ {...}, {...} ]` — không có `hydra:member` |
| `text/html` | **406** |

MIME type hợp lệ (lấy từ body lỗi 406):

```
application/ld+json
application/vnd.openapi+json
application/hal+json
application/vnd.api+json
application/json
application/xml
text/xml
application/x-yaml
text/csv
```

**Khuyến nghị:** nếu muốn code ngắn thì gửi `Accept: application/json` để nhận mảng thẳng, nhưng khi đó **mất** `hydra:totalItems` và `hydra:view` (không đếm được tổng/trang kế).

---

## 2. Workflow 5 bước

```
GET  /domains          -> lấy domain đang active
POST /accounts         -> tạo hộp mail      {address, password}
POST /token            -> lấy JWT           {address, password}
GET  /messages         -> poll hộp thư      (Authorization: Bearer)
GET  /messages/{id}    -> nội dung đầy đủ
```

`POST /accounts` và `POST /token` là 2 endpoint **không** cần Bearer. Còn lại đều cần.

---

## 3. Chi tiết endpoint

### `GET /domains`

**Params:** `page` (int, optional)

```json
{
  "hydra:totalItems": 1,
  "hydra:member": [
    {
      "@id": "/domains/6aa6b995482bea6f4a97f585",
      "@type": "Domain",
      "id": "6aa6b995482bea6f4a97f585",
      "domain": "maxxspace.com",
      "isActive": true,
      "isPrivate": false,
      "createdAt": "2026-09-13T00:00:00+00:00",
      "updatedAt": "2026-09-13T00:00:00+00:00"
    }
  ]
}
```

- Chỉ chọn domain có `isActive: true` và `isPrivate: false`.
- Địa chỉ = `"<tên>" + "@" + domain`.
- **Thực tế hiện tại chỉ có 1 domain** (`maxxspace.com`) — đừng assume list dài.

### `GET /domains/{id}`

Lấy 1 domain theo id.

---

### `POST /accounts` — tạo hộp mail

**Body**

| Field | Type | Bắt buộc |
|---|---|---|
| `address` | string | ✅ — phải thuộc domain hợp lệ |
| `password` | string | ✅ |

```bash
curl -s -X POST https://api.mail.tm/accounts \
  -H 'Content-Type: application/json' \
  -d '{"address":"user@maxxspace.com","password":"Secret123!"}'
```

**Response 201**

```json
{
  "@id": "/accounts/6ac4a160c582daea8c075f34",
  "@type": "Account",
  "id": "6ac4a160c582daea8c075f34",
  "address": "user@maxxspace.com",
  "quota": 40000000,
  "used": 0,
  "isDisabled": false,
  "isDeleted": false,
  "createdAt": "2026-10-06T07:21:04+00:00",
  "updatedAt": "2026-10-06T07:21:04+00:00"
}
```

| Field | Ghi chú |
|---|---|
| `id` | dùng cho `GET/DELETE /accounts/{id}` và topic SSE |
| `quota` | byte, thực tế trả `40000000` (~40 MB) |
| `used` | byte đã dùng |
| `address` đã tồn tại | `422` |

> Lưu `id` và `address` lại — `POST /token` cần `address`, không dùng `id`.

### `GET /accounts/{id}`

Cùng schema. Phải dùng đúng Bearer của account đó.

### `DELETE /accounts/{id}`

Xoá vĩnh viễn, không khôi phục. **204** nếu thành công.

### `GET /me`

Trả account ứng với Bearer đang dùng. Dùng để **check token còn sống không**.

---

### `POST /token` — đăng nhập / lấy JWT

**Body**

| Field | Type |
|---|---|
| `address` | string |
| `password` | string |

```bash
curl -s -X POST https://api.mail.tm/token \
  -H 'Content-Type: application/json' \
  -d '{"address":"user@maxxspace.com","password":"Secret123!"}'
```

**Response 200**

```json
{
  "token": "eyJ0eXAiOiJKV1Q...",
  "@id": "/accounts/6ac4a160c582daea8c075f34",
  "id": "6ac4a160c582daea8c075f34"
}
```

Token là JWT (HS512), payload chứa `roles`, `address`, `id`, và `mercure.subscribe` (topic SSE).

Dùng ở mọi request khác:

```
Authorization: Bearer <token>
```

**Lỗi**

| Status | Body | Nghĩa |
|---|---|---|
| `401` | `{"code":401,"message":"Invalid credentials."}` | sai email/mật khẩu |
| `401` | `{"code":401,"message":"JWT Token not found"}` | thiếu/không có Bearer |
| `405` | — | sai method (đừng `GET /token`) |

---

### `GET /messages` — danh sách thư

**Params:** `page` (int)

Cần Bearer.

```bash
curl -s https://api.mail.tm/messages \
  -H "Authorization: Bearer $TOKEN"
```

**Response**

```json
{
  "hydra:totalItems": 0,
  "hydra:member": [
    {
      "@id": "/messages/xxxx",
      "id": "xxxx",
      "accountId": "6ac4a160c582daea8c075f34",
      "msgid": "<...@mx.x>",
      "from": { "name": "Blaze", "address": "no-reply@blaze.vn" },
      "to": [ { "name": "", "address": "user@maxxspace.com" } ],
      "subject": "Xác thực email của bạn",
      "intro": "Bấm vào link để xác thực...",
      "seen": false,
      "isDeleted": false,
      "hasAttachments": false,
      "size": 4820,
      "downloadUrl": "/messages/xxxx/download",
      "createdAt": "2026-10-06T07:22:10+00:00",
      "updatedAt": "2026-10-06T07:22:10+00:00"
    }
  ],
  "hydra:view": {
    "hydra:first": "/messages?page=1",
    "hydra:last": "/messages?page=1"
  }
}
```

- Tối đa **30 tin / trang**.
- `intro` = preview, **có** ở list, **không có** ở detail.

### `GET /messages/{id}` — nội dung đầy đủ

```json
{
  "id": "xxxx",
  "accountId": "...",
  "msgid": "<...>",
  "from": { "name": "Blaze", "address": "no-reply@blaze.vn" },
  "to": [ { "name": "", "address": "user@maxxspace.com" } ],
  "cc": [],
  "bcc": [],
  "subject": "Xác thực email của bạn",
  "seen": false,
  "flagged": false,
  "isDeleted": false,
  "verifications": [],
  "retention": true,
  "retentionDate": "2026-10-13T07:22:10+00:00",
  "text": "plain text version...",
  "html": ["<html>...version..."],
  "hasAttachments": false,
  "attachments": [],
  "size": 4820,
  "downloadUrl": "/messages/xxxx/download",
  "createdAt": "2026-10-06T07:22:10+00:00",
  "updatedAt": "2026-10-06T07:22:10+00:00"
}
```

- `text` = plain text, `html` = mảng các part HTML.
- **`retentionDate`** = hạn lưu. Sau đó mail bị xoá.
- `attachments[]` mỗi phần: `id`, `filename`, `contentType`, `disposition`, `transferEncoding`, `related`, `size`, `downloadUrl`.

**Kéo link xác thực từ mail:** regex trên `html[0]` (hoặc `text`) ra URL, vì đa số service gửi nút/button chứ không gửi link trần.

### `PATCH /messages/{id}` — đánh dấu đã đọc

Body: `{ "seen": true }` → `200` `{"seen": true}`.

### `DELETE /messages/{id}` — xoá

Không body → **204**.

### `GET /sources/{id}` — mail thô (RFC 822)

```json
{
  "id": "xxxx",
  "downloadUrl": "/messages/xxxx/source",
  "data": "Received: from ... \nSubject: ... \n\nbody..."
}
```

### `GET /attachments/{attachmentId}`

**Lưu ý encoding:** file nhị phân phải tải như mảng số nguyên / bytes, **không** decode string. Dựa vào `contentType` để chọn cách xử lý.

---

## 4. Real-time (SSE qua Mercure) — không phải webhook

Thay vì poll, subscribe SSE:

| Hạng mục | Giá trị |
|---|---|
| Hub | `https://mercure.mail.tm/.well-known/mercure` |
| Topic | `/accounts/{id}` |
| Auth | header `Authorization: Bearer <token>` |

Mỗi lần có mail đến sẽ push 1 event kiểu `Account` — chính là resource account đó với `used` đã cập nhật. Nhận xong event thì gọi `GET /messages`.

---

## 5. Mã lỗi HTTP

| Status | Ý nghĩa |
|---|---|
| `200` / `201` / `204` | thành công |
| `400` | payload thiếu field |
| `401` | token sai hoặc thiếu Bearer |
| `404` | account/message không tồn tại |
| `405` | sai HTTP method |
| `418` | teapot — thật đấy |
| `422` | payload sai logic (username quá ngắn, domain không hợp lệ, email trùng) |
| `429` | vượt **8 QPS / IP** — giãn thời gian ra |
| `406` | `Accept` không được hỗ trợ |

Body lỗi dạng:

```json
{ "code": 401, "message": "Invalid credentials." }
```

hoặc dạng hydra error:

```json
{ "@id": "/errors/406", "@type": "hydra:Error", "title": "An error occurred", "detail": "..." }
```

Validation lỗi trả `ConstraintViolationList`:

```json
{
  "status": 422,
  "violations": [
    { "propertyPath": "address", "message": "..." }
  ]
}
```

---

## 6. Script end-to-end (đã chạy thật)

```bash
#!/usr/bin/env bash
set -euo pipefail
API=https://api.mail.tm

# 1. domain đang active (Accept: json -> mảng thẳng)
DOMAIN=$(curl -s -H 'Accept: application/json' "$API/domains" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)[0]["domain"])')

ADDR="$(head -c 6 /dev/urandom | xxd -p | head -c 10)@${DOMAIN}"
PASS="Aa1!$(date +%s | tail -c 6)"
echo "inbox: $ADDR"

# 2. tạo account
curl -s -X POST "$API/accounts" -H 'Content-Type: application/json' \
  -d "{\"address\":\"$ADDR\",\"password\":\"$PASS\"}" > /dev/null

# 3. token
TOKEN=$(curl -s -X POST "$API/token" -H 'Content-Type: application/json' \
  -d "{\"address\":\"$ADDR\",\"password\":\"$PASS\"}" \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

# 4. poll mail tối đa 60s
for i in $(seq 1 30); do
  N=$(curl -s "$API/messages" -H "Authorization: Bearer $TOKEN" \
      | python3 -c 'import sys,json;print(json.load(sys.stdin)["hydra:totalItems"])')
  [ "$N" != "0" ] && break
  sleep 2
done

# 5. lấy nội dung
curl -s "$API/messages" -H "Authorization: Bearer $TOKEN" \
  | python3 -c '
import sys,json
m=json.load(sys.stdin)["hydra:member"][0]
print(m["id"], m["from"]["address"], m["subject"])
'
```

---

## 7. Python (không dependency)

```python
import json, random, string, time, urllib.request

API = "https://api.mail.tm"

def call(path, method="GET", body=None, token=None):
    req = urllib.request.Request(API + path, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")   # -> mảng thẳng
    if token:
        req.add_header("Authorization", "Bearer " + token)
    data = json.dumps(body).encode() if body is not None else None
    try:
        with urllib.request.urlopen(req, data, timeout=20) as r:
            raw = r.read().decode()
            return r.status, json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]

def new_inbox():
    _, domains = call("/domains")
    domain = domains[0]["domain"]                      # chỉ domain[0] là active
    addr = "opx" + "".join(random.choices(string.ascii_lowercase + string.digits, k=10)) + "@" + domain
    pw = "Passw0rd!" + str(random.randint(1000, 9999))
    _, acc = call("/accounts", "POST", {"address": addr, "password": pw})
    _, tok = call("/token", "POST", {"address": addr, "password": pw})
    return acc["id"], addr, tok["token"]

def wait_mail(token, timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        st, msgs = call("/messages", token=token)
        if st == 200 and msgs:
            return msgs[0]
        time.sleep(2)
    return None
```

---

## 8. Ghi chú vận hành

- **8 QPS** tính theo IP. Poll 1 lần / 2s là an toàn tuyệt đối.
- Token không có hạn rõ ràng trong docs — nếu `401` thì đăng nhập lại bằng `POST /token`.
- Mail có hạn lưu (`retentionDate`), đừng assume còn mãi.
- Domain có thể đổi bất cứ lúc nào — **luôn** gọi `GET /domains` trước, hard-code domain là vỡ.
- Attribution: link về `https://mail.tm` ở chỗ dễ thấy nếu build sản phẩm dùng API này.
