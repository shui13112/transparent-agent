import logging
import shutil

import chromadb
from pathlib import Path

logger = logging.getLogger(__name__)

from llama_index.core import VectorStoreIndex, StorageContext, Document, Settings
from llama_index.core.indices.utils import embed_nodes
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.core.node_parser import SentenceSplitter
from config import get_settings
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.embeddings.openai import OpenAIEmbedding
from utils.knowledge_file_db import KnowledgeFileDB


class KnowledgeBaseManager:
    def __init__(
        self,
        persist_dir: str = "./chroma_db",
        chunk_size: int = 512,
        chunk_overlap: int = 50,
    ):
        """
        初始化知识库管理器
        """

        self.persist_dir = persist_dir
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

        backend_dir = Path(__file__).resolve().parent.parent
        self.knowledge_dir = str(backend_dir / "knowledge")

        # 两个独立的 Collection 名称
        self.STATIC_COLLECTION_NAME = "static_docs" # 存 PDF/TXT
        self.DYNAMIC_COLLECTION_NAME = "dynamic_web" # 存 JSON 网页



        #  初始化分块器 (类属性)
        self.text_splitter = SentenceSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap
        )

        # 静态文档向量库 — 全局共享，长期持久
        self._static_db = chromadb.PersistentClient(
            path=str(Path(self.persist_dir) / "static")
        )
        # 动态网页向量库 — 每个会话独立数据库，会话删除时一并清理
        self._session_clients: dict[str, chromadb.PersistentClient] = {}

        # 初始化 SQLite 文件追踪器，每次实例化时自动同步静态库
        self._file_db = KnowledgeFileDB(str(Path(self.persist_dir) / "knowledge_files.db"))
        self._cleanup_orphan_sessions()
        if self._setup_models():
            self._sync_static_files()

    def get_session_client(self, session_id: str) -> chromadb.PersistentClient:
        """Get or create a ChromaDB client for a specific session."""
        if session_id not in self._session_clients:
            session_dir = str(Path(self.persist_dir) / "sessions" / session_id)
            self._session_clients[session_id] = chromadb.PersistentClient(path=session_dir)
        return self._session_clients[session_id]

    # 类级别的嵌入模型加载状态，确保只加载一次
    _embed_model_loaded: bool = False

    @staticmethod
    def _resolve_model_path(model_name: str) -> str:
        """在本地缓存中查找 HuggingFace 模型，找到则返回本地路径，否则返回原始名称。

        避免 sentence-transformers 传入 repo ID 时强制联网检查 adapter_config.json。
        """
        import os as _os

        # 收集去重后的搜索根目录
        roots: set[str] = set()
        hf_home = _os.environ.get("HF_HOME")
        if hf_home:
            roots.add(hf_home)                            # 直接放置
            roots.add(str(Path(hf_home) / "hub"))         # HF Hub 默认布局
        llama = _os.environ.get("LLAMA_INDEX_CACHE_DIR")
        if llama:
            roots.add(llama)
        hf_cache = _os.environ.get("HF_HUB_CACHE")
        if hf_cache:
            roots.add(hf_cache)
        # 始终包含 HuggingFace 默认缓存目录
        default_cache = str(Path.home() / ".cache" / "huggingface" / "hub")
        roots.add(default_cache)

        dirnames = [
            f"models--{model_name.replace('/', '--')}",
            model_name.replace("/", "--"),
            model_name.split("/")[-1] if "/" in model_name else model_name,
        ]
        for root in roots:
            for dn in dirnames:
                base = Path(root) / dn
                if not base.is_dir():
                    # 尝试 snapshot 子目录
                    base = Path(root) / dn / "snapshots"
                    if base.is_dir():
                        snaps = sorted(base.iterdir(), reverse=True)
                        for snap in snaps:
                            if list(snap.glob("*.safetensors")) or list(snap.glob("pytorch_model.bin")):
                                return str(snap)
                    continue
                if list(base.glob("*.safetensors")) or list(base.glob("pytorch_model.bin")):
                    return str(base)
                # HF Hub cache: model files live in snapshots/<hash>/, not directly in base
                snapshots_dir = base / "snapshots"
                if snapshots_dir.is_dir():
                    snaps = sorted(snapshots_dir.iterdir(), reverse=True)
                    for snap in snaps:
                        if list(snap.glob("*.safetensors")) or list(snap.glob("pytorch_model.bin")):
                            return str(snap)
        return model_name

    def _setup_models(self) -> bool:
        """配置 Embedding 模型。返回 True 表示配置成功，False 表示未配置。

        模型只会在首次调用时加载，后续调用直接返回已有结果。
        """
        if KnowledgeBaseManager._embed_model_loaded:
            return True
        setting = get_settings()
        embedding_model = setting.embedding_model
        if not embedding_model:
            logger.warning(">>> [系统] 未配置 Embedding 模型，向量检索不可用。")
            return False
        if embedding_model == "BAAI/bge-m3":
            model_path = self._resolve_model_path("BAAI/bge-m3")
            if model_path != "BAAI/bge-m3":
                logger.info(">>> [系统] 加载本地 BAAI/bge-m3 Embedding 模型: %s", model_path)
                Settings.embed_model = HuggingFaceEmbedding(model_name=model_path)
            else:
                logger.info(">>> [系统] 加载 BAAI/bge-m3 Embedding 模型（在线）...")
                Settings.embed_model = HuggingFaceEmbedding(model_name="BAAI/bge-m3")
        else:
            from llama_index.embeddings.dashscope import DashScopeEmbedding
            embedding_api_key = setting.embedding_api_key
            embedding_base_url = setting.embedding_base_url
            logger.info(f">>> [系统] 加载 {embedding_model} Embedding 模型...")
            try:
                Settings.embed_model = DashScopeEmbedding(
                model=embedding_model,
                api_key=embedding_api_key,
                api_base=embedding_base_url,
            )
            except Exception as e:
                logger.error(f">>> [系统] 加载 {embedding_model} Embedding 模型失败: {e}")
                raise ValueError(f"不支持的 Embedding 类型: {embedding_model}")
        KnowledgeBaseManager._embed_model_loaded = True
        return True
            
  

    def _sync_static_files(self):
        """增量同步静态知识库：检测新增/删除的文件，按需更新 ChromaDB 和 SQLite。

        每次 KnowledgeBaseManager 实例化时自动调用，确保向量库与磁盘文件保持一致。
        """
        knowledge_path = Path(self.knowledge_dir)
        if not knowledge_path.exists():
            return

        # 收集磁盘上的 PDF/TXT 文件
        current_files: dict[str, str] = {}
        for ext in (".pdf", ".txt"):
            for f in knowledge_path.glob(f"*{ext}"):
                current_files[f.name] = str(f)

        current_names = set(current_files.keys())
        db_names = self._file_db.get_existing_names()

        new_names = current_names - db_names
        stale_records = self._file_db.find_deleted(current_names)

        if not new_names and not stale_records:
            return

        collection = self._static_db.get_or_create_collection(self.STATIC_COLLECTION_NAME)
        vector_store = ChromaVectorStore(chroma_collection=collection)

        # 删除已不存在文件的所有切片
        for record in stale_records:
            try:
                collection.delete(where={"parent_id": record["id"]})
            except Exception:
                pass  # ChromaDB 某些版本对不存在的 metadata 键可能报错
            self._file_db.delete(record["id"])
            logger.info(f">>> [静态库] 已删除文件及切片: {record['file_name']}")

        # 处理新增文件
        if new_names:
            new_paths = [current_files[name] for name in new_names]
            documents = self._load_and_merge_static_files(
                data_dir=self.knowledge_dir, file_paths=new_paths
            )
            # 为每个文档附加 parent_id（SQLite 主键）
            for doc in documents:
                file_name = doc.metadata.get("file_name", "")
                file_path = current_files.get(file_name, "")
                file_id = self._file_db.insert(file_name, file_path)
                doc.metadata["parent_id"] = file_id

            nodes = self.text_splitter.get_nodes_from_documents(documents)
            id_to_embed_map = embed_nodes(nodes, Settings.embed_model, show_progress=True)
            for node in nodes:
                node.embedding = id_to_embed_map.get(node.node_id)
            vector_store.add(nodes)
            logger.info(f">>> [静态库] 新增 {len(new_names)} 个文件，共 {len(nodes)} 个切片，已入库。")

    def _cleanup_orphan_sessions(self):
        """清理已不存在会话对应的动态向量数据库。

        扫描 chroma_db/sessions/ 下的会话目录，若 sessions/{id}.json 已删除，
        则删除对应的 chroma_db/sessions/{id}/ 目录。
        """
        sessions_json_dir = Path(self.persist_dir).parent / "sessions"
        sessions_chroma_dir = Path(self.persist_dir) / "sessions"

        if not sessions_chroma_dir.exists():
            return

        valid_ids = {
            p.stem for p in sessions_json_dir.glob("*.json")
            if p.stem != "archive"
        }

        for session_dir in sessions_chroma_dir.iterdir():
            if not session_dir.is_dir():
                continue
            if session_dir.name not in valid_ids:
                shutil.rmtree(session_dir, ignore_errors=True)
                logger.info(f">>> [动态库] 已清理孤儿会话数据库: {session_dir.name}")

    # ==================== 第一部分：静态文件 (PDF/TXT) 处理 ====================

    def _load_and_merge_static_files(self, data_dir: str, file_paths: list[str] | None = None):
        """内部方法：读取 PDF/TXT 并解决跨页语义断裂问题。

        PDF 使用 PyMuPDFLoader（与抓取器一致，提取质量优于 SimpleDirectoryReader），
        TXT 直接读取。若提供 file_paths，则仅加载指定文件；否则扫描整个 data_dir。
        """
        from langchain_community.document_loaders import PyMuPDFLoader

        if file_paths:
            files = [Path(p) for p in file_paths]
        else:
            files: list[Path] = []
            for ext in (".pdf", ".txt"):
                files.extend(Path(data_dir).glob(f"*{ext}"))

        final_documents: list[Document] = []
        for file_path in files:
            file_name = file_path.name
            suffix = file_path.suffix.lower()

            if suffix == ".pdf":
                loader = PyMuPDFLoader(str(file_path))
                pages = loader.load()
                text = "\n\n".join(
                    p.page_content for p in pages if p.page_content.strip()
                )
            elif suffix == ".txt":
                text = file_path.read_text(encoding="utf-8")
            else:
                continue

            if not text.strip():
                continue

            final_documents.append(Document(
                text=text,
                metadata={
                    "file_name": file_name,
                    "source_type": "static_file",
                },
            ))

        return final_documents

    def build_static_index(self, data_dir: str = None):
        """构建或加载静态文档知识库索引（用于查询）。

        增量同步已在 __init__ 中完成，此方法仅负责加载已有索引供查询。
        若 collection 为空（极端情况，如同步时未配置模型），会全量构建作为兜底。
        """
        if data_dir is None:
            data_dir = self.knowledge_dir
        logger.info(f"\n>>> [静态库] 正在扫描目录: {data_dir}")
        collection = self._static_db.get_or_create_collection(self.STATIC_COLLECTION_NAME)
        vector_store = ChromaVectorStore(chroma_collection=collection)
        storage_context = StorageContext.from_defaults(vector_store=vector_store)

        if collection.count() > 0:
            logger.info(f">>> [静态库] 本地已存在 {collection.count()} 个切片，直接加载。")
            return VectorStoreIndex.from_vector_store(vector_store, storage_context=storage_context)

        # 兜底：全量构建并写入 SQLite
        documents = self._load_and_merge_static_files(data_dir)
        for doc in documents:
            file_name = doc.metadata.get("file_name", "")
            file_path = str(Path(data_dir) / file_name)
            existing = self._file_db.get_by_name(file_name)
            if existing:
                file_id = existing["id"]
            else:
                file_id = self._file_db.insert(file_name, file_path)
            doc.metadata["parent_id"] = file_id

        nodes = self.text_splitter.get_nodes_from_documents(documents)
        logger.info(f">>> [静态库] 分块完毕，共 {len(nodes)} 个节点，正在存入数据库...")

        index = VectorStoreIndex(nodes, storage_context=storage_context, show_progress=True)
        return index

    # ==================== 第二部分：动态网页向量库 ====================

    def get_dynamic_index(self, session_id: str):
        """加载指定会话的动态网页向量索引（仅加载，不重建）。

        session_id 为必传参数，索引必须已由 web_search_tool 写入。
        """
        db = self.get_session_client(session_id)
        try:
            collection = db.get_collection(self.DYNAMIC_COLLECTION_NAME)
        except ValueError:
            logger.info(f">>> [动态库] 会话 {session_id} 的动态集合不存在。")
            return None
        if collection.count() == 0:
            logger.info(f">>> [动态库] 会话 {session_id} 的动态集合为空。")
            return None
        logger.info(f">>> [动态库] 加载会话 {session_id} 的动态索引，共 {collection.count()} 个切片。")
        vector_store = ChromaVectorStore(chroma_collection=collection)
        storage_context = StorageContext.from_defaults(vector_store=vector_store)
        return VectorStoreIndex.from_vector_store(vector_store, storage_context=storage_context)

    def build_dynamic_index_from_docs(self, documents: list, session_id: str = ""):
        """直接从 LangChain Document 列表构建或增量更新动态网页向量索引。

        每个会话拥有独立的向量数据库，session_id 为必传参数。
        若已有索引，则将新文档增量添加；否则全量构建。
        """
        from llama_index.core import Document as LlamaDocument

        db = self.get_session_client(session_id)

        collection = db.get_or_create_collection(self.DYNAMIC_COLLECTION_NAME)
        vector_store = ChromaVectorStore(chroma_collection=collection)
        storage_context = StorageContext.from_defaults(vector_store=vector_store)

        if collection.count() > 0:
            logger.info(f">>> [动态库] 本地已存在 {collection.count()} 个切片。")
            if not documents:
                return VectorStoreIndex.from_vector_store(vector_store, storage_context=storage_context)
            llama_docs = [
                LlamaDocument(text=doc.page_content, metadata=doc.metadata)
                for doc in documents
            ]
            logger.info(f">>> [动态库] 正在将 {len(llama_docs)} 个新文档增量添加到已有索引...")
            nodes = self.text_splitter.get_nodes_from_documents(llama_docs)
            id_to_embed_map = embed_nodes(nodes, Settings.embed_model, show_progress=True)
            for node in nodes:
                node.embedding = id_to_embed_map.get(node.node_id)
            vector_store.add(nodes)
            logger.info(f">>> [动态库] 增量添加完成，新增 {len(nodes)} 个切片，总计 {collection.count()} 个。")
            return VectorStoreIndex.from_vector_store(vector_store, storage_context=storage_context)

        llama_docs = [
            LlamaDocument(
                text=doc.page_content,
                metadata=doc.metadata,
            )
            for doc in documents
        ]
        logger.info(f">>> [动态库] 正在分块，{len(llama_docs)} 个文档...")
        nodes = self.text_splitter.get_nodes_from_documents(llama_docs)
        logger.info(f">>> [动态库] 分块完毕，共 {len(nodes)} 个节点，正在向量化（调用 embedding API）...")

        index = VectorStoreIndex(nodes, storage_context=storage_context, show_progress=True)
        logger.info(f">>> [动态库] 向量化完成，已存入数据库。")
        return index

    def clear_dynamic_index(self, session_id: str = ""):
        """【一键删除】清空指定会话的动态网页向量数据，不影响静态文件和其他会话。"""
        db = self.get_session_client(session_id)
        try:
            db.delete_collection(self.DYNAMIC_COLLECTION_NAME)
            logger.info(">>> [动态库] 网页集合已成功删除！")
        except ValueError:
            logger.info(">>> [动态库] 集合不存在，无需删除。")

    def get_web_content_by_url(self, session_id: str, url: str) -> dict | None:
        """按 URL 查询已缓存的网页抓取全文。"""
        from utils.web_cache_db import WebCacheDB
        cache_db = WebCacheDB(f"{self.persist_dir}/sessions/{session_id}/web_cache.db")
        return cache_db.get_by_url(url)

    @staticmethod
    def delete_session_index(base_dir: Path, session_id: str) -> None:
        """删除指定会话的 ChromaDB 向量数据库目录。"""
        session_dir = base_dir / "chroma_db" / "sessions" / session_id
        if session_dir.exists():
            shutil.rmtree(session_dir, ignore_errors=True)