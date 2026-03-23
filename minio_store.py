import os
import logging
from datetime import timedelta
from dotenv import load_dotenv
from minio import Minio
from minio.error import S3Error
import json
import urllib3
import certifi


# ==========================================
# 日誌配置類別 (Pythonic 封裝)
# ==========================================
class LoggerFactory:
    """
    負責生成與設定標準化日誌記錄器 (Logger) 的工廠類別。
    """

    @staticmethod
    def get_logger(name: str) -> logging.Logger:
        """
        取得一個配置好格式與輸出處理器的 Logger 實例。

        Args:
            name (str): 日誌記錄器的名稱，通常傳入 __name__ 即可。

        Returns:
            logging.Logger: 配置完成的標準庫 Logger 實例。
        """
        logger = logging.getLogger(name)
        if not logger.handlers:
            logger.setLevel(logging.INFO)
            formatter = logging.Formatter(
                fmt='%(asctime)s [%(levelname)s] %(name)s : %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            handler = logging.StreamHandler()
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        return logger


# 在模組層級初始化 logger，這是 Python 最標準的做法
logger = LoggerFactory.get_logger(__name__)


# ==========================================
# 核心儲存類別
# ==========================================
class MinioStore:
    """
    封裝 MinIO (S3 兼容) 對象儲存核心操作的類別。
    負責初始化連線並提供標準的 CRUD 及預簽名 URL 產出方法。
    """

    def __init__(self):
        """
        初始化 MinioStore 實例。
        自 .env 檔案讀取設定，包含主機、通訊埠、存取金鑰等，並建立 MinIO 客戶端連線。

        Raises:
            ValueError: 當 .env 檔案缺少必要的連線設定時拋出。
            Exception: MinIO 客戶端初始化失敗時拋出。
        """
        load_dotenv()

        host = os.getenv("MINIO_HOST")
        port = os.getenv("MINIO_PORT")
        access_key = os.getenv("MINIO_ACCESS_KEY")
        secret_key = os.getenv("MINIO_SECRET_KEY")
        secure_str = os.getenv("MINIO_SECURE", "False")
        secure = secure_str.lower() in ("true", "1", "t")

        # 讀取 Cloudflare Service Token (如果沒有設定，可以為空)
        cf_client_id = os.getenv("CF_ACCESS_CLIENT_ID")
        cf_client_secret = os.getenv("CF_ACCESS_CLIENT_SECRET")

        if not all([host, port, access_key, secret_key]):
            logger.error("環境變數缺失，請檢查 .env 檔案是否包含 HOST, PORT, ACCESS_KEY, SECRET_KEY。")
            raise ValueError("缺少必要的 MinIO 環境變數")

        # 將網域與通訊埠重新組合為 SDK 需要的 endpoint 格式
        endpoint = f"{host}:{port}"

        # 安全地遮蔽密碼 (保留前後各2碼，隱藏中間)
        masked_secret = f"{secret_key[:2]}****{secret_key[-2:]}" if len(secret_key) > 4 else "****"

        logger.info("正在初始化 MinIO 客戶端...")
        logger.info(
            f"配置參數 -> Endpoint: {endpoint}, AccessKey: {access_key}, SecretKey: {masked_secret}, Secure: {secure}"
        )

        try:
            # ==========================================
            # 核心修改區塊：建立帶有 Cloudflare Headers 的 HTTP Client
            # ==========================================
            custom_headers = {}
            if cf_client_id and cf_client_secret:
                custom_headers["CF-Access-Client-Id"] = cf_client_id
                custom_headers["CF-Access-Client-Secret"] = cf_client_secret
                logger.info("🔒 已掛載 Cloudflare Zero Trust Headers。")

            # 建立自訂的連線池
            # 根據 secure 參數決定是否需要驗證 SSL 憑證
            http_client = urllib3.PoolManager(
                timeout=urllib3.Timeout.DEFAULT_TIMEOUT,
                cert_reqs='CERT_REQUIRED' if secure else 'CERT_NONE',
                ca_certs=certifi.where() if secure else None,
                headers=custom_headers  # <--- 在這裡把 Headers 塞進去
            )

            # 將自訂的 http_client 傳入 Minio
            self._client = Minio(
                endpoint=endpoint,
                access_key=access_key,
                secret_key=secret_key,
                secure=secure,
                http_client=http_client  # <--- 關鍵參數
            )
            logger.info("MinIO 客戶端初始化完成。")
        except Exception as e:
            logger.error(f"初始化失敗: {e}")
            raise

    def ensure_bucket_exists(self, bucket_name: str):
        """
        檢查指定的 Bucket 是否存在，若不存在則自動建立。

        Args:
            bucket_name (str): 欲檢查或建立的儲存桶名稱。

        Raises:
            S3Error: 操作 Bucket 失敗時拋出。
        """
        try:
            if not self._client.bucket_exists(bucket_name):
                self._client.make_bucket(bucket_name)
                logger.info(f"Bucket '{bucket_name}' 建立成功。")
            else:
                logger.info(f"Bucket '{bucket_name}' 已存在。")
        except S3Error as err:
            logger.error(f"檢查或建立 Bucket 失敗: {err}")
            raise

    def upload_file(self, bucket_name: str, object_name: str, file_path: str):
        """
        將本機檔案上傳至指定的 Bucket 之中。

        Args:
            bucket_name (str): 目標儲存桶名稱。
            object_name (str): 上傳後在 MinIO 上的對象名稱 (可包含路徑，例如 'folder/file.txt')。
            file_path (str): 本機端要上傳的檔案實體路徑。

        Raises:
            S3Error: 檔案上傳失敗時拋出。
        """
        try:
            self._client.fput_object(bucket_name, object_name, file_path)
            logger.info(f"檔案 '{file_path}' 成功上傳為 '{object_name}'。")
        except S3Error as err:
            logger.error(f"上傳失敗: {err}")
            raise

    def download_file(self, bucket_name: str, object_name: str, file_path: str):
        """
        自指定的 Bucket 下載檔案至本機。

        Args:
            bucket_name (str): 來源儲存桶名稱。
            object_name (str): MinIO 上的對象名稱。
            file_path (str): 下載至本機的目標路徑與檔名。

        Raises:
            S3Error: 檔案下載失敗時拋出。
        """
        try:
            self._client.fget_object(bucket_name, object_name, file_path)
            logger.info(f"檔案 '{object_name}' 成功下載至 '{file_path}'。")
        except S3Error as err:
            logger.error(f"下載失敗: {err}")
            raise

    def delete_file(self, bucket_name: str, object_name: str):
        """
        刪除指定 Bucket 內的目標檔案。

        Args:
            bucket_name (str): 目標儲存桶名稱。
            object_name (str): 欲刪除的對象名稱。

        Raises:
            S3Error: 檔案刪除失敗時拋出。
        """
        try:
            self._client.remove_object(bucket_name, object_name)
            logger.info(f"檔案 '{object_name}' 已成功刪除。")
        except S3Error as err:
            logger.error(f"刪除失敗: {err}")
            raise

    def list_files(self, bucket_name: str):
        """
        列出指定 Bucket 內的所有檔案清單。
        此方法會遞迴掃描，將印出對象名稱與檔案大小。

        Args:
            bucket_name (str): 欲查詢的儲存桶名稱。

        Raises:
            S3Error: 讀取檔案清單失敗時拋出。
        """
        try:
            objects = self._client.list_objects(bucket_name, recursive=True)
            logger.info(f"開始列出 Bucket '{bucket_name}' 內的檔案：")
            for obj in objects:
                logger.info(f"  -> 檔案: {obj.object_name} (大小: {obj.size} bytes)")
        except S3Error as err:
            logger.error(f"讀取清單失敗: {err}")
            raise

    def get_presigned_url(self, bucket_name: str, object_name: str, expires_days: int = 7) -> str:
        """
        產生帶有時效性的預簽名下載網址 (Presigned URL)。
        適合用於私有 Bucket 分享臨時存取權限給外部系統。

        Args:
            bucket_name (str): 目標儲存桶名稱。
            object_name (str): 欲產生連結的對象名稱。
            expires_days (int, optional): URL 的有效天數，預設為 7 天 (MinIO 上限通常為 7 天)。

        Returns:
            str: 產生的預簽名 URL 字串。若發生錯誤則回傳空字串。
        """
        try:
            url = self._client.presigned_get_object(
                bucket_name,
                object_name,
                expires=timedelta(days=expires_days)
            )
            logger.info(f"產生預簽名 URL 成功 (有效期限 {expires_days} 天)")
            return url
        except S3Error as err:
            logger.error(f"產生 URL 失敗: {err}")
            return ""

    def set_bucket_public_read(self, bucket_name: str):
        """
        透過 Policy 將指定的 Bucket 設置為永久公開唯讀。
        """
        # 定義 S3 標準的公開唯讀 Policy
        policy = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": ["*"]},
                    "Action": ["s3:GetBucketLocation", "s3:GetObject"],
                    "Resource": [f"arn:aws:s3:::{bucket_name}", f"arn:aws:s3:::{bucket_name}/*"]
                }
            ]
        }

        try:
            # 將 Dict 轉為 JSON 字串並寫入 MinIO
            self._client.set_bucket_policy(bucket_name, json.dumps(policy))
            logger.info(f"✅ 已成功將 Bucket '{bucket_name}' 權限設置為 [公開唯讀]")
        except Exception as e:
            logger.error(f"❌ 設置公開權限失敗: {e}")


# ==========================================
# 測試入口
# ==========================================
if __name__ == "__main__":
    test_logger = LoggerFactory.get_logger("MainTest")
    test_logger.info("=== 開始測試 MinIO CRUD ===")

    test_file = "test.txt"
    if not os.path.exists(test_file):
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("這是一段 MinIO 的測試文字！")

    try:
        store = MinioStore()

        TEST_BUCKET = "anki-media"
        TEST_OBJECT_NAME = "test_audio_001.txt"
        DOWNLOAD_PATH = "downloaded_test.txt"

        store.ensure_bucket_exists(TEST_BUCKET)
        store.set_bucket_public_read(TEST_BUCKET)
        store.upload_file(TEST_BUCKET, TEST_OBJECT_NAME, test_file)
        store.list_files(TEST_BUCKET)

        url = store.get_presigned_url(TEST_BUCKET, TEST_OBJECT_NAME)
        test_logger.info(f"取得的 URL: \n{url}")

    except Exception as e:
        test_logger.error(f"測試過程中發生未預期的錯誤: {e}", exc_info=True)

    test_logger.info("=== 測試結束 ===")