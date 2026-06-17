"""
Embedding 速度测试脚本。
测试 BAAI/bge-m3 模型对本地 knowledge/ 目录 PDF 文件的切块 + embedding 速度。
仅做内存中测试，不写入任何数据库。

用法:
    cd backend
    $env:HF_ENDPOINT = "https://hf-mirror.com"   # 国内必需
    python benchmark_embedding.py                 # 测试前 2 个 PDF
    python benchmark_embedding.py --all           # 测试全部 PDF
    python benchmark_embedding.py --pages 3       # 每个 PDF 只取前 3 页
"""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import torch
from llama_index.core import Settings, Document
from llama_index.core.node_parser import SentenceSplitter
from llama_index.core.indices.utils import embed_nodes
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from langchain_community.document_loaders import PyMuPDFLoader

CHUNK_SIZE = 512
CHUNK_OVERLAP = 50
MODEL_NAME = "BAAI/bge-m3"

BACKEND_DIR = Path(__file__).resolve().parent
KNOWLEDGE_DIR = BACKEND_DIR / "knowledge"


def find_local_model(model_name: str) -> str | None:
    """在本地 HF 缓存中查找模型，返回可直接加载的路径或 None。

    兼容多种缓存结构：
      {HF_HOME}/hub/models--org--model/snapshots/xxx/   (huggingface_hub 默认)
      {HF_HOME}/models--org--model/                     (HF_HUB_CACHE 直接指向 HF_HOME)
      {HF_HOME}/model/                                  (snapshot_download --local-dir 扁平结构)
      {HF_HOME}/org--model/                             (同上)
    """
    org, model = model_name.split("/") if "/" in model_name else ("", model_name)
    dirnames = [
        f"models--{model_name.replace('/', '--')}",   # models--BAAI--bge-m3
        f"{org}--{model}",                            # BAAI--bge-m3
        model,                                         # bge-m3
    ]
    hf_home = os.environ.get("HF_HOME", os.path.join(os.path.expanduser("~"), ".cache", "huggingface"))
    hf_hub_cache = os.environ.get("HF_HUB_CACHE", str(Path(hf_home) / "hub"))

    # snapshot 子目录（huggingface_hub 缓存格式）
    for dn in dirnames:
        for root in [Path(hf_home) / "hub", Path(hf_home), Path(hf_hub_cache)]:
            base = root / dn / "snapshots"
            if not base.exists():
                continue
            for snap in sorted(base.iterdir(), reverse=True):
                files = list(snap.glob("*.safetensors")) + list(snap.glob("pytorch_model.bin"))
                if files:
                    return str(snap)

    # 扁平结构（--local-dir 或 ModelScope 下载格式）
    for dn in dirnames:
        for root in [Path(hf_home), Path(hf_hub_cache)]:
            base = root / dn
            if not base.is_dir():
                continue
            files = list(base.glob("*.safetensors")) + list(base.glob("pytorch_model.bin"))
            if files:
                return str(base)

    return None


def get_pdf_paths() -> list[Path]:
    return sorted(p for p in KNOWLEDGE_DIR.glob("*.pdf") if p.stat().st_size > 0)


def load_pdf(file_path: Path, max_pages: int = 0) -> str:
    loader = PyMuPDFLoader(str(file_path))
    pages = loader.load()
    if max_pages > 0:
        pages = pages[:max_pages]
    return "\n\n".join(p.page_content for p in pages if p.page_content.strip())


def main():
    parser = argparse.ArgumentParser(description="Embedding 速度测试")
    parser.add_argument("--all", action="store_true", help="测试全部 PDF")
    parser.add_argument("--count", type=int, default=2, help="测试前 N 个 PDF")
    parser.add_argument("--pages", type=int, default=0, help="每 PDF 最多取几页（0=全部）")
    parser.add_argument("--local-only", action="store_true", help="仅使用本地缓存，不联网")
    args = parser.parse_args()

    # ── 环境信息 ──
    hf_endpoint = os.environ.get("HF_ENDPOINT", "")
    print(f"{'='*60}")
    print(f"模型: {MODEL_NAME}")
    print(f"HF_HOME: {os.environ.get('HF_HOME', '默认')}")
    if hf_endpoint:
        print(f"HF_ENDPOINT: {hf_endpoint}")
    print(f"CUDA 可用: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"GPU: {torch.cuda.get_device_name(0)}")
        mem_gb = torch.cuda.get_device_properties(0).total_mem / 1024**3
        print(f"显存: {mem_gb:.1f} GB")
    else:
        print("运行设备: CPU")
    print(f"分块: {CHUNK_SIZE} / 重叠: {CHUNK_OVERLAP}")

    # ── 查找 / 下载模型 ──
    local_path = find_local_model(MODEL_NAME)
    if local_path:
        print(f"本地缓存: {local_path}")
    elif args.local_only:
        print("错误：--local-only 但本地无缓存模型。")
        if not hf_endpoint:
            print('提示：$env:HF_ENDPOINT = "https://hf-mirror.com"')
        return
    else:
        print("本地无缓存，从 HuggingFace 下载...")
        if not hf_endpoint:
            print('提示：若下载失败，$env:HF_ENDPOINT = "https://hf-mirror.com"')

    print(f"\n{'='*60}")
    print("加载 Embedding 模型...")
    t0 = time.perf_counter()
    embed_model = HuggingFaceEmbedding(
        model_name=local_path or MODEL_NAME,
        local_files_only=bool(local_path),
    )
    Settings.embed_model = embed_model
    load_time = time.perf_counter() - t0
    print(f"加载耗时: {load_time:.1f}s")
    try:
        print(f"模型设备: {embed_model._model.device}")
    except Exception:
        pass

    # ── 加载 PDF ──
    pdf_paths = get_pdf_paths()
    if not pdf_paths:
        print("\n未找到 PDF。")
        return
    if not args.all:
        pdf_paths = pdf_paths[:args.count]

    print(f"\n{'='*60}")
    print(f"PDF 数量: {len(pdf_paths)}")
    for p in pdf_paths:
        print(f"  {p.name}  ({p.stat().st_size / 1024**2:.1f} MB)")

    all_documents: list[Document] = []
    total_load_time = 0.0
    for path in pdf_paths:
        t0 = time.perf_counter()
        text = load_pdf(path, max_pages=args.pages)
        elapsed = time.perf_counter() - t0
        total_load_time += elapsed
        if text.strip():
            all_documents.append(Document(
                text=text,
                metadata={"file_name": path.name, "source_type": "static_file"},
            ))
        print(f"  加载: {path.name}  ({len(text):,} 字符)  {elapsed:.1f}s")

    if not all_documents:
        print("无有效文档。")
        return

    # ── 切块 ──
    print(f"\n{'='*60}")
    print("切块中...")
    t0 = time.perf_counter()
    nodes = SentenceSplitter(chunk_size=CHUNK_SIZE, chunk_overlap=CHUNK_OVERLAP).get_nodes_from_documents(all_documents)
    chunk_time = time.perf_counter() - t0
    print(f"完成: {len(nodes)} chunks  ({chunk_time:.1f}s)")

    # ── Embedding ──
    print(f"\n{'='*60}")
    print(f"向量化 {len(nodes)} chunks...")
    t0 = time.perf_counter()
    embed_nodes(nodes, Settings.embed_model, show_progress=True)
    embed_time = time.perf_counter() - t0

    # ── 结果 ──
    print(f"\n{'='*60}")
    print("结果汇总")
    print(f"{'='*60}")
    total_chars = sum(len(d.text) for d in all_documents)
    print(f"  PDF 数:        {len(pdf_paths)}")
    print(f"  总字符:        {total_chars:,}")
    print(f"  Chunk 数:      {len(nodes)}")
    print(f"  模型加载:      {load_time:.1f}s")
    print(f"  PDF 加载:      {total_load_time:.1f}s")
    print(f"  切块:          {chunk_time:.1f}s")
    print(f"  向量化:        {embed_time:.1f}s")
    print(f"  总耗时:        {load_time + total_load_time + chunk_time + embed_time:.1f}s")
    if len(nodes) > 0:
        print(f"  每 chunk:      {embed_time / len(nodes) * 1000:.0f} ms")
        print(f"  吞吐量:        {len(nodes) / embed_time:.1f} chunks/s")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
