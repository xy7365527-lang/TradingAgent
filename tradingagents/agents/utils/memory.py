import os
import numpy as np

# 兼容 numpy 2.0 移除的别名，供 chromadb 旧版本导入使用
if not hasattr(np, "float_"):
    np.float_ = np.float64  # type: ignore[attr-defined]
if not hasattr(np, "int_"):
    np.int_ = np.int64  # type: ignore[attr-defined]
if not hasattr(np, "uint"):
    np.uint = np.uint64  # type: ignore[attr-defined]

# 关闭 Chroma 遥测，避免缺失 posthog 模块导致异常
os.environ.setdefault("CHROMA_TELEMETRY_IMPLEMENTATION", "none")

try:
    import chromadb  # type: ignore[import-not-found]
    from chromadb.config import Settings  # type: ignore[import-not-found]
except Exception:  # noqa: BLE001
    chromadb = None  # type: ignore[assignment]
    Settings = None  # type: ignore[assignment]
from openai import OpenAI


class FinancialSituationMemory:
    def __init__(self, name, config):
        if config["backend_url"] == "http://localhost:11434/v1":
            self.embedding = "nomic-embed-text"
        else:
            self.embedding = "text-embedding-3-small"
        self.client = OpenAI(base_url=config["backend_url"])
        self.use_chroma = False
        self.situation_collection = None
        self._local_store: list[dict[str, object]] = []

        # 尝试初始化 chroma；失败则退化为本地存储
        try:
            if chromadb is not None and Settings is not None:
                self.chroma_client = chromadb.Client(Settings(allow_reset=True))  # type: ignore[operator]
                self.situation_collection = self.chroma_client.create_collection(name=name)
                self.use_chroma = True
        except Exception:
            self.use_chroma = False
            self.situation_collection = None

    def get_embedding(self, text):
        """Get OpenAI embedding for a text"""
        
        response = self.client.embeddings.create(
            model=self.embedding, input=text
        )
        return response.data[0].embedding

    def add_situations(self, situations_and_advice):
        """Add financial situations and their corresponding advice. Parameter is a list of tuples (situation, rec)"""

        situations = []
        advice = []
        ids = []
        embeddings = []

        if self.use_chroma and self.situation_collection is not None:
            offset = self.situation_collection.count()
            for i, (situation, recommendation) in enumerate(situations_and_advice):
                situations.append(situation)
                advice.append(recommendation)
                ids.append(str(offset + i))
                embeddings.append(self.get_embedding(situation))
            self.situation_collection.add(
                documents=situations,
                metadatas=[{"recommendation": rec} for rec in advice],
                embeddings=embeddings,
                ids=ids,
            )
        else:
            # 退化为本地内存向量库
            start = len(self._local_store)
            for i, (situation, recommendation) in enumerate(situations_and_advice):
                emb = self.get_embedding(situation)
                self._local_store.append(
                    {
                        "id": str(start + i),
                        "situation": situation,
                        "recommendation": recommendation,
                        "embedding": np.asarray(emb, dtype=np.float64),
                    }
                )

    def get_memories(self, current_situation, n_matches=1):
        """Find matching recommendations using OpenAI embeddings"""
        if self.use_chroma and self.situation_collection is not None:
            query_embedding = self.get_embedding(current_situation)
            results = self.situation_collection.query(
                query_embeddings=[query_embedding],
                n_results=n_matches,
                include=["metadatas", "documents", "distances"],
            )
            matched_results = []
            for i in range(len(results["documents"][0])):
                matched_results.append(
                    {
                        "matched_situation": results["documents"][0][i],
                        "recommendation": results["metadatas"][0][i]["recommendation"],
                        "similarity_score": 1 - results["distances"][0][i],
                    }
                )
            return matched_results
        else:
            # 本地余弦相似度检索
            if not self._local_store:
                return []
            qe = np.asarray(self.get_embedding(current_situation), dtype=np.float64)
            qn = np.linalg.norm(qe) + 1e-12
            scored: list[tuple[float, dict[str, object]]] = []
            for row in self._local_store:
                ve = row["embedding"]  # type: ignore[index]
                vn = np.linalg.norm(ve) + 1e-12
                sim = float(np.dot(qe, ve) / (qn * vn))
                scored.append((sim, row))
            scored.sort(key=lambda x: x[0], reverse=True)
            top = scored[: max(1, int(n_matches))]
            return [
                {
                    "matched_situation": r["situation"],  # type: ignore[index]
                    "recommendation": r["recommendation"],  # type: ignore[index]
                    "similarity_score": s,
                }
                for s, r in top
            ]


if __name__ == "__main__":
    # Example usage
    matcher = FinancialSituationMemory()

    # Example data
    example_data = [
        (
            "High inflation rate with rising interest rates and declining consumer spending",
            "Consider defensive sectors like consumer staples and utilities. Review fixed-income portfolio duration.",
        ),
        (
            "Tech sector showing high volatility with increasing institutional selling pressure",
            "Reduce exposure to high-growth tech stocks. Look for value opportunities in established tech companies with strong cash flows.",
        ),
        (
            "Strong dollar affecting emerging markets with increasing forex volatility",
            "Hedge currency exposure in international positions. Consider reducing allocation to emerging market debt.",
        ),
        (
            "Market showing signs of sector rotation with rising yields",
            "Rebalance portfolio to maintain target allocations. Consider increasing exposure to sectors benefiting from higher rates.",
        ),
    ]

    # Add the example situations and recommendations
    matcher.add_situations(example_data)

    # Example query
    current_situation = """
    Market showing increased volatility in tech sector, with institutional investors 
    reducing positions and rising interest rates affecting growth stock valuations
    """

    try:
        recommendations = matcher.get_memories(current_situation, n_matches=2)

        for i, rec in enumerate(recommendations, 1):
            print(f"\nMatch {i}:")
            print(f"Similarity Score: {rec['similarity_score']:.2f}")
            print(f"Matched Situation: {rec['matched_situation']}")
            print(f"Recommendation: {rec['recommendation']}")

    except Exception as e:
        print(f"Error during recommendation: {str(e)}")
