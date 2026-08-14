"""
load_cfs_models.py -- embed + upsert rag_v2/cfs_models.jsonl into the `cfs_opensees_models`
collection. Run on the RAG box (same venv/model as reingest_v2):

    cd /home/mikemolt54321/rag/rebuild_v2
    sudo -u mikemolt54321 env MODEL_CACHE=/home/mikemolt54321/rag/models \
      /home/mikemolt54321/rag/venv/bin/python load_cfs_models.py cfs_models.jsonl

Recreates the collection FRESH each run (the jsonl is the source of truth -- 22 docs since
2026-07-30: W01-W16 walls + P01-P06 portals). Embeds with the same Nomic model, no prefix,
matching local_api's query embedding. 22 docs: seconds, no need to stop the query service.
"""
import json, os, sys, uuid, hashlib

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_NAME = "nomic-ai/nomic-embed-text-v1"
MODEL_CACHE = os.environ.get("MODEL_CACHE", os.path.join(HERE, "models"))
COLLECTION = "cfs_opensees_models"


def main(path):
    from sentence_transformers import SentenceTransformer
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams, PointStruct
    docs = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    print("loading embedding model ...", flush=True)
    model = SentenceTransformer(MODEL_NAME, trust_remote_code=True, cache_folder=MODEL_CACHE)
    vecs = model.encode([d["text"][:3500] for d in docs], show_progress_bar=False, batch_size=8)
    client = QdrantClient(host=os.environ.get("QDRANT_HOST", "localhost"),
                          port=int(os.environ.get("QDRANT_PORT", "6333")))
    if client.collection_exists(COLLECTION):
        client.delete_collection(COLLECTION)
    client.create_collection(COLLECTION,
                             vectors_config=VectorParams(size=768, distance=Distance.COSINE))
    points = [PointStruct(
        id=str(uuid.UUID(hex=hashlib.md5(("cfsmodel-" + d["building_id"]).encode()).hexdigest())),
        vector=v.tolist(), payload=d) for d, v in zip(docs, vecs)]
    client.upsert(COLLECTION, points=points)
    print("upserted %d models -> %s" % (len(points), COLLECTION), flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "cfs_models.jsonl"))
