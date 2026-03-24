# MinIO Storage Client with Cloudflare Zero Trust Support
**整合 Cloudflare Zero Trust 的 MinIO 儲存客戶端**

A robust, production-ready Python client for MinIO (S3-compatible) object storage. This module encapsulates standard S3 CRUD operations, dynamic logging, and crucially, a custom HTTP transport interceptor to seamlessly bypass Cloudflare Zero Trust protections.

這是一個 MinIO (S3 兼容) Python 客戶端。本模組封裝了標準的 S3 CRUD 操作與動態日誌系統，內建了專屬的 HTTP 傳輸層攔截器，能無縫穿透 Cloudflare Zero Trust 的安全防護。

---

## Key Features / 核心功能

- **Zero Trust Compatibility (Zero Trust 兼容)**: 
  *EN:* Bypasses the limitation of the official `minio-py` SDK where custom headers are stripped during internal calls. A custom `CFAuthPoolManager` overrides the underlying `urlopen` method to forcefully inject `CF-Access-Client-Id` and `CF-Access-Client-Secret`.
  *ZH:* 突破官方 `minio-py` SDK 在內部調用時會丟棄自訂標頭的限制。透過自訂 `CFAuthPoolManager` 覆寫底層 `urlopen` 方法，強制注入 Cloudflare 服務權杖。

- **Dynamic Logging (動態日誌)**: 
  *EN:* Built-in `LoggerFactory` that supports dynamic log level switching (e.g., `INFO`, `DEBUG`) via environment variables without changing the code.
  *ZH:* 內建 `LoggerFactory`，支援透過環境變數動態切換日誌級別（如 `INFO`, `DEBUG`），排查問題無需修改程式碼。

- **Standard S3 Operations (標準 S3 操作)**: 
  *EN:* Streamlined methods for Bucket validation, File Upload/Download, Deletion, and generating both Presigned and Permanent URLs.
  *ZH:* 提供極簡化的方法來執行儲存桶驗證、檔案上傳/下載、刪除，以及產出預簽名與永久公開網址。

---

## Configuration / 設定檔

*EN:* Please refer to the included `.env.example` file to configure your MinIO connection parameters and optional Cloudflare Zero Trust tokens. Copy it to `.env` before running the application.
*ZH:* 請參考專案內附的 `.env.example` 檔案來設定您的 MinIO 連線參數與非必填的 Cloudflare Zero Trust 權杖。在執行程式前，請將該檔案複製並重新命名為 `.env`。

---

## Architecture / 架構說明

### 1. LoggerFactory
*EN:* A Pythonic factory class for standardizing log output. It pre-loads the `.env` file and adjusts the verbosity of the application based on the `LOG_LEVEL` variable, ensuring clean output in production and detailed traces during debugging.
*ZH:* 一個 Pythonic 的工廠類別，用於標準化日誌輸出。它會預先讀取 `.env` 並根據 `LOG_LEVEL` 調整系統輸出，確保生產環境日誌乾淨，除錯時具備詳細追蹤能力。

### 2. CFAuthPoolManager (The Core Hack / 核心攔截器)
*EN:* The official MinIO Python SDK bypasses standard request wrappers and calls `urllib3`'s low-level `urlopen` directly for operations like `_get_region()`. This class inherits from `urllib3.PoolManager` and acts as a middleware interceptor, guaranteeing that Cloudflare Service Tokens are merged into the payload milliseconds before the socket transmission.
*ZH:* 由於官方 MinIO SDK 會繞過標準請求封裝，直接呼叫底層的 `urlopen`（例如在獲取 Region 時），導致自訂 Header 遺失。此類別繼承自 `urllib3.PoolManager` 作為中介攔截器，保證在封包送進網卡的前一刻，強制寫入 Cloudflare 服務權杖。

### 3. MinioStore
*EN:* The main facade class interacting with the storage server. It automatically determines whether to use the standard HTTP pool or the `CFAuthPoolManager` based on the presence of Cloudflare credentials.
*ZH:* 與儲存伺服器互動的主要門面類別 (Facade)。它會根據是否偵測到 Cloudflare 憑證，自動決定要使用標準 HTTP 連線池或是 `CFAuthPoolManager` 攔截器。

---

## Quick Start / 快速開始

*EN:* Using the `MinioStore` class is incredibly simple. It automatically handles authentication and region discovery behind the scenes.
*ZH:* 使用 `MinioStore` 類別非常直覺，它會在背景自動處理複雜的認證與區域探索。

```python
from minio_store import MinioStore

# 1. Initialize the client (Auto-loads .env and injects headers if needed)
# 初始化客戶端 (自動讀取 .env 並在需要時掛載 CF 標頭)
store = MinioStore()

BUCKET_NAME = "anki-media"
FILE_PATH = "local_audio.mp3"
OBJECT_NAME = "audio/local_audio.mp3"

# 2. Ensure bucket exists / 確保儲存桶存在
store.ensure_bucket_exists(BUCKET_NAME)

# 3. Set bucket to public read (Optional) / 將儲存桶設為公開唯讀 (非必填)
store.set_bucket_public_read(BUCKET_NAME)

# 4. Upload a file / 上傳檔案
store.upload_file(BUCKET_NAME, OBJECT_NAME, FILE_PATH)

# 5. Get a permanent direct link / 取得永久直連網址
url = store.get_permanent_url(BUCKET_NAME, OBJECT_NAME)
print(f"File accessible at: {url}")
