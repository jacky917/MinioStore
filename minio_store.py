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
# Logger Configuration Class (Pythonic Wrapper)
# ==========================================
class LoggerFactory:
    """
    負責生成與設定標準化日誌記錄器 (Logger) 的工廠類別。
    A factory class responsible for generating and configuring standardized loggers.
    """

    @staticmethod
    def get_logger(name: str) -> logging.Logger:
        """
        取得一個配置好格式與輸出處理器的 Logger 實例。
        Get a Logger instance configured with specific formatting and stream handlers.

        Args:
            name (str): 日誌記錄器的名稱，通常傳入 __name__ 即可。
                        The name of the logger, usually __name__ is passed.

        Returns:
            logging.Logger: 配置完成的標準庫 Logger 實例。
                            A fully configured standard library Logger instance.
        """
        # 預先載入 dotenv 以便讀取 LOG_LEVEL
        # Pre-load dotenv to read the LOG_LEVEL environment variable
        load_dotenv()

        logger = logging.getLogger(name)
        if not logger.handlers:
            # 從環境變數讀取日誌級別，若無設定則預設為 INFO
            # Read log level from environment variables, default to INFO if not set
            log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
            log_level = getattr(logging, log_level_str, logging.INFO)

            logger.setLevel(log_level)
            formatter = logging.Formatter(
                fmt='%(asctime)s [%(levelname)s] %(name)s : %(message)s',
                datefmt='%Y-%m-%d %H:%M:%S'
            )
            handler = logging.StreamHandler()
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        return logger


# 在模組層級初始化 logger
# Initialize the logger at the module level
logger = LoggerFactory.get_logger(__name__)


# ==========================================
# 自訂底層攔截器 (解決 MinIO SDK 忽略 Header 的問題)
# Custom Underlying Interceptor (Solves MinIO SDK's issue of ignoring Headers)
# ==========================================
class CFAuthPoolManager(urllib3.PoolManager):
    """
    自訂的 urllib3 連線池。
    專門用來攔截 minio-py 底層的 urlopen 呼叫，強制在每一筆請求發送前注入 Cloudflare Headers。

    A custom urllib3 PoolManager.
    Specifically designed to intercept the underlying 'urlopen' calls of minio-py,
    forcing the injection of Cloudflare Headers before every request is sent.
    """

    def __init__(self, cf_client_id: str, cf_client_secret: str, **kwargs):
        super().__init__(**kwargs)
        # 初始化時儲存 Cloudflare 的認證資訊
        # Store Cloudflare authentication credentials during initialization
        self.cf_headers = {
            "CF-Access-Client-Id": cf_client_id,
            "CF-Access-Client-Secret": cf_client_secret
        }

    def urlopen(self, method, url, redirect=True, **kw):
        """
        覆寫 urllib3 的 urlopen 方法來攔截並修改請求標頭。
        Override urllib3's urlopen method to intercept and modify request headers.
        """
        # 取得原先請求中準備發送的 headers 字典，若無則預設為空字典
        # Retrieve the headers dictionary prepared for the original request, default to empty dict if none
        headers = kw.get('headers', {})

        # 將 Cloudflare Token 強制合併/覆寫進去目前的 headers 中
        # Forcibly merge/overwrite the Cloudflare Tokens into the current headers
        headers.update(self.cf_headers)

        # 將修改後的 headers 重新賦值回關鍵字參數字典中
        # Reassign the modified headers back into the keyword arguments dictionary
        kw['headers'] = headers

        # 放行：呼叫父類別 (urllib3.PoolManager) 原本的 urlopen 方法，繼續執行底層的 HTTP 請求
        # Pass-through: Call the original urlopen method of the parent class to continue the underlying HTTP request
        return super().urlopen(method, url, redirect, **kw)


# ==========================================
# 核心儲存類別
# Core Storage Class
# ==========================================
class MinioStore:
    """
    封裝 MinIO (S3 兼容) 對象儲存核心操作的類別。
    負責初始化連線並提供標準的 CRUD 及預簽名 URL 產出方法。

    A class that encapsulates the core operations for MinIO (S3 compatible) object storage.
    Responsible for initializing the connection and providing standard CRUD and presigned URL generation methods.
    """

    def __init__(self):
        """
        初始化 MinioStore 實例。
        自 .env 檔案讀取設定，包含主機、通訊埠、存取金鑰等，並建立 MinIO 客戶端連線。

        Initialize the MinioStore instance.
        Reads configurations from the .env file, including host, port, access key, etc., and establishes the MinIO client connection.

        Raises:
            ValueError: 當 .env 檔案缺少必要的連線設定時拋出。 / Raised when essential connection settings are missing in .env.
            Exception: MinIO 客戶端初始化失敗時拋出。 / Raised when the MinIO client fails to initialize.
        """
        logger.debug("開始初始化 MinioStore 實例...")
        load_dotenv()

        # 逐一讀取環境變數 / Read environment variables one by one
        self._host = os.getenv("MINIO_HOST")
        self._port = os.getenv("MINIO_PORT")
        access_key = os.getenv("MINIO_ACCESS_KEY")
        secret_key = os.getenv("MINIO_SECRET_KEY")
        secure_str = os.getenv("MINIO_SECURE", "False")

        # 將字串布林值安全轉換為 Python 的 bool 型別 / Safely convert string boolean to Python bool type
        self._secure = secure_str.lower() in ("true", "1", "t")

        # 讀取 Cloudflare Service Token (如果沒有設定，可以為空)
        # Read Cloudflare Service Token (can be empty if not configured)
        cf_client_id = os.getenv("CF_ACCESS_CLIENT_ID")
        cf_client_secret = os.getenv("CF_ACCESS_CLIENT_SECRET")

        # 檢查核心環境變數是否齊全 / Check if core environment variables are complete
        if not all([self._host, self._port, access_key, secret_key]):
            logger.error("環境變數缺失，請檢查 .env 檔案是否包含 HOST, PORT, ACCESS_KEY, SECRET_KEY。")
            raise ValueError("缺少必要的 MinIO 環境變數")

        # 安全地遮蔽密碼 (保留前後各2碼，隱藏中間) 避免日誌外洩敏感資訊
        # Safely mask the secret key (keep the first and last 2 characters, hide the middle) to prevent logging sensitive info
        masked_secret = f"{secret_key[:2]}****{secret_key[-2:]}" if len(secret_key) > 4 else "****"
        # 對 Cloudflare Secret 做同樣的遮蔽處理 (保留前5碼)
        # Apply the same masking to the Cloudflare Secret (keep the first 5 characters)
        masked_cf_secret = f"{cf_client_secret[:5]}****" if cf_client_secret and len(cf_client_secret) > 5 else "****"

        logger.info("正在初始化 MinIO 客戶端...")
        logger.debug(f"連線參數細節 -> Host: {self._host}, Port: {self._port}, Secure: {self._secure}")
        logger.debug(f"認證參數細節 -> AccessKey: {access_key}, SecretKey: {masked_secret}")

        try:
            # 決定憑證驗證級別：若為 HTTPS (secure)，則要求驗證憑證；否則不驗證
            # Determine cert verification level: require cert if HTTPS (secure), else none
            cert_reqs = 'CERT_REQUIRED' if self._secure else 'CERT_NONE'
            logger.debug(f"urllib3 連線池設定 -> timeout: DEFAULT, cert_reqs: {cert_reqs}")

            # ==========================================
            # 核心修改區塊：根據有無 CF Token 決定是否使用攔截器
            # Core modification block: Decide whether to use the interceptor based on the presence of CF Tokens
            # ==========================================
            if cf_client_id and cf_client_secret:
                # 若環境變數包含 CF 認證，則觸發攔截器機制
                # If env vars contain CF credentials, trigger the interceptor mechanism
                logger.info("🔒 使用 CFAuthPoolManager 強制攔截並注入 Zero Trust Headers。")
                logger.debug(
                    f"掛載 Headers 內容 -> CF-Access-Client-Id: {cf_client_id}, CF-Access-Client-Secret: {masked_cf_secret}")

                # 實例化我們自訂的 CFAuthPoolManager，將 CF credentials 傳入
                # Instantiate our custom CFAuthPoolManager, passing in the CF credentials
                http_client = CFAuthPoolManager(
                    cf_client_id=cf_client_id,
                    cf_client_secret=cf_client_secret,
                    timeout=urllib3.Timeout.DEFAULT_TIMEOUT,
                    cert_reqs=cert_reqs,
                    # 若為 HTTPS，使用 certifi 提供可信的 CA 根憑證包
                    # If HTTPS, use certifi to provide a trusted CA root certificate bundle
                    ca_certs=certifi.where() if self._secure else None
                )
            else:
                # 若無 CF 認證，退回使用標準的 urllib3 連線池
                # If no CF credentials, fallback to the standard urllib3 PoolManager
                logger.debug("未偵測到 CF Token，使用標準 urllib3.PoolManager。")
                http_client = urllib3.PoolManager(
                    timeout=urllib3.Timeout.DEFAULT_TIMEOUT,
                    cert_reqs=cert_reqs,
                    ca_certs=certifi.where() if self._secure else None
                )

            # 優化 endpoint，標準的 80 或 443 端口不需顯式指定
            # Optimize endpoint, standard ports 80 or 443 do not need to be explicitly specified
            # 這能避免 Cloudflare 驗證 Hostname 時因帶有 :443 導致不匹配的問題
            # This prevents Hostname mismatch issues during Cloudflare validation when :443 is appended
            clean_endpoint = self._host
            if self._port and self._port not in ["80", "443"]:
                # 只有在使用非標準 port 時，才將 port 拼接在 host 後面
                # Only append the port to the host if a non-standard port is being used
                clean_endpoint = f"{self._host}:{self._port}"

            logger.debug(f"傳入 MinIO 的 Clean Endpoint: {clean_endpoint}")

            # 將上述配置好的 (可能包含攔截器的) http_client 以及連線資訊傳入 Minio 客戶端進行初始化
            # Pass the configured http_client (potentially with interceptor) and connection info to initialize the Minio client
            self._client = Minio(
                endpoint=clean_endpoint,
                access_key=access_key,
                secret_key=secret_key,
                secure=self._secure,
                http_client=http_client
            )
            logger.info("MinIO 客戶端初始化完成。")
        except Exception as e:
            logger.error(f"初始化失敗: {e}", exc_info=True)
            raise

    def ensure_bucket_exists(self, bucket_name: str):
        """
        檢查指定的 Bucket 是否存在，若不存在則自動建立。
        Check if the specified Bucket exists, and automatically create it if it doesn't.
        """
        logger.debug(f"[方法調用] ensure_bucket_exists -> bucket_name: '{bucket_name}'")
        try:
            if not self._client.bucket_exists(bucket_name):
                self._client.make_bucket(bucket_name)
                logger.info(f"Bucket '{bucket_name}' 建立成功。")
            else:
                logger.info(f"Bucket '{bucket_name}' 已存在。")
        except S3Error as err:
            logger.error(f"檢查或建立 Bucket 失敗: {err}", exc_info=True)
            raise

    def upload_file(self, bucket_name: str, object_name: str, file_path: str):
        """
        將本機檔案上傳至指定的 Bucket 之中。
        Upload a local file into the specified Bucket.
        """
        logger.debug(f"[方法調用] upload_file -> bucket: '{bucket_name}', object: '{object_name}', file: '{file_path}'")
        try:
            self._client.fput_object(bucket_name, object_name, file_path)
            logger.info(f"檔案 '{file_path}' 成功上傳為 '{object_name}'。")
        except S3Error as err:
            logger.error(f"上傳失敗: {err}", exc_info=True)
            raise

    def download_file(self, bucket_name: str, object_name: str, file_path: str):
        """
        自指定的 Bucket 下載檔案至本機。
        Download a file from the specified Bucket to the local machine.
        """
        logger.debug(f"[方法調用] download_file -> bucket: '{bucket_name}', object: '{object_name}'")
        try:
            self._client.fget_object(bucket_name, object_name, file_path)
            logger.info(f"檔案 '{object_name}' 成功下載至 '{file_path}'。")
        except S3Error as err:
            logger.error(f"下載失敗: {err}", exc_info=True)
            raise

    def delete_file(self, bucket_name: str, object_name: str):
        """
        刪除指定 Bucket 內的目標檔案。
        Delete the target file within the specified Bucket.
        """
        logger.debug(f"[方法調用] delete_file -> bucket: '{bucket_name}', object: '{object_name}'")
        try:
            self._client.remove_object(bucket_name, object_name)
            logger.info(f"檔案 '{object_name}' 已成功刪除。")
        except S3Error as err:
            logger.error(f"刪除失敗: {err}", exc_info=True)
            raise

    def list_files(self, bucket_name: str):
        """
        列出指定 Bucket 內的所有檔案清單。
        List all files within the specified Bucket.
        """
        logger.debug(f"[方法調用] list_files -> bucket: '{bucket_name}'")
        try:
            objects = self._client.list_objects(bucket_name, recursive=True)
            logger.info(f"開始列出 Bucket '{bucket_name}' 內的檔案：")
            for obj in objects:
                logger.info(f"  -> 檔案: {obj.object_name} (大小: {obj.size} bytes)")
        except S3Error as err:
            logger.error(f"讀取清單失敗: {err}", exc_info=True)
            raise

    def get_presigned_url(self, bucket_name: str, object_name: str, expires_days: int = 7) -> str:
        """
        產生帶有時效性的預簽名下載網址 (Presigned URL)。
        Generate a time-limited presigned download URL.
        """
        logger.debug(f"[方法調用] get_presigned_url -> bucket: '{bucket_name}', object: '{object_name}'")
        try:
            url = self._client.presigned_get_object(
                bucket_name,
                object_name,
                expires=timedelta(days=expires_days)
            )
            logger.info(f"產生預簽名 URL 成功 (有效期限 {expires_days} 天)")
            return url
        except S3Error as err:
            logger.error(f"產生 URL 失敗: {err}", exc_info=True)
            return ""

    def get_permanent_url(self, bucket_name: str, object_name: str) -> str:
        """
        產出永久性的公開直連網址。
        前提：必須先執行過 set_bucket_public_read()

        Generate a permanent public direct link.
        Prerequisite: set_bucket_public_read() must have been executed previously.
        """
        logger.debug(f"[方法調用] get_permanent_url -> bucket: '{bucket_name}', object: '{object_name}'")
        protocol = "https" if self._secure else "http"

        if self._port in ["443", "80"]:
            url = f"{protocol}://{self._host}/{bucket_name}/{object_name}"
        else:
            url = f"{protocol}://{self._host}:{self._port}/{bucket_name}/{object_name}"

        logger.info(f"✅ 產生永久連結: {url}")
        return url

    def set_bucket_public_read(self, bucket_name: str):
        """
        透過 Policy 將指定的 Bucket 設置為永久公開唯讀。
        Set the specified Bucket to permanent public read-only access via a Policy.
        """
        logger.debug(f"[方法調用] set_bucket_public_read -> bucket: '{bucket_name}'")
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
            self._client.set_bucket_policy(bucket_name, json.dumps(policy))
            logger.info(f"✅ 已成功將 Bucket '{bucket_name}' 權限設置為 [公開唯讀]")
        except Exception as e:
            logger.error(f"❌ 設置公開權限失敗: {e}", exc_info=True)


# ==========================================
# 測試入口
# Test Entry Point
# ==========================================
if __name__ == "__main__":
    test_logger = LoggerFactory.get_logger("MainTest")
    test_logger.info("=== 開始測試 MinIO CRUD ===")

    # 建立一個測試用的本地端假檔案 / Create a dummy local file for testing
    test_file = "test.txt"
    if not os.path.exists(test_file):
        with open(test_file, "w", encoding="utf-8") as f:
            f.write("這是一段 MinIO 的測試文字！ / This is a MinIO test text!")

    try:
        store = MinioStore()

        TEST_BUCKET = "anki-media"
        TEST_OBJECT_NAME = "test_audio_001.txt"
        DOWNLOAD_PATH = "downloaded_test.txt"

        # 執行一系列的測試操作 / Execute a series of test operations
        store.ensure_bucket_exists(TEST_BUCKET)
        store.set_bucket_public_read(TEST_BUCKET)
        store.upload_file(TEST_BUCKET, TEST_OBJECT_NAME, test_file)
        store.list_files(TEST_BUCKET)

        # url = store.get_presigned_url(TEST_BUCKET, TEST_OBJECT_NAME)
        url = store.get_permanent_url(TEST_BUCKET, TEST_OBJECT_NAME)
        test_logger.info(f"取得的 URL: \n{url}")

    except Exception as e:
        test_logger.error(f"測試過程中發生未預期的錯誤: {e}", exc_info=True)

    test_logger.info("=== 測試結束 ===")