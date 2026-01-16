import argparse
from app.services.java_parser import JavaProjectParser
from app.services.neo4j_service import Neo4jService
from app.services.graph_builder import GraphBuilder
from app.services.superimpose_service import SuperimposeService

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--left_path", help="Left repo path")
    ap.add_argument("--right_path", help="Right repo path")
    ap.add_argument("--project_name", default="java-project")
    ap.add_argument("--left_repo_id", default="left")
    ap.add_argument("--right_repo_id", default="right")
    ap.add_argument("--supergraph_id", default="compare")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    parser = JavaProjectParser()
    neo = Neo4jService()
    try:
        if args.left_path:
            g = parser.parse_directory(args.left_path, args.project_name, args.left_repo_id)
            if args.overwrite:
                neo.delete_repo(args.project_name, args.left_repo_id)
            GraphBuilder(neo).upsert_repo_graph(g)
        if args.right_path:
            g = parser.parse_directory(args.right_path, args.project_name, args.right_repo_id)
            if args.overwrite:
                neo.delete_repo(args.project_name, args.right_repo_id)
            GraphBuilder(neo).upsert_repo_graph(g)

        if args.left_path and args.right_path:
            s = SuperimposeService(neo)
            s.delete_supergraph(args.supergraph_id)
            print(s.superimpose_and_diff(args.project_name, args.left_repo_id, args.right_repo_id, args.supergraph_id))
    finally:
        neo.close()

if __name__ == "__main__":
    main()
