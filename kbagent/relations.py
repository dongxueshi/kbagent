"""Wiki-link indexing and scoped graph queries, mixed into the MySQL Store."""

from collections import defaultdict, deque

from .wikilinks import parse_links


class LinkStore:
    def _initialize_links(self, db):
        if not db.execute("SELECT 1 FROM schema_meta WHERE `key`='wiki_links_v1'").fetchone():
            for document in db.execute("SELECT * FROM documents").fetchall():
                self._index_links(db, dict(document))
            for row in db.execute("SELECT id FROM spaces").fetchall():
                self._resolve_pending(db, row[0])
            db.execute("INSERT INTO schema_meta VALUES ('wiki_links_v1','1')")

    @staticmethod
    def _name_index(db, space_id):
        names = defaultdict(dict)
        documents = {}
        for row in db.execute("SELECT id,title FROM documents WHERE space_id=?", (space_id,)):
            documents[row["id"]] = dict(row)
            names[row["title"]][row["id"]] = dict(row)
        for row in db.execute(
            """SELECT d.id,d.title,a.name FROM document_aliases a
            JOIN documents d ON d.id=a.document_id WHERE d.space_id=?""",
            (space_id,),
        ):
            names[row["name"]][row["id"]] = {"id": row["id"], "title": row["title"]}
        return names, documents

    @staticmethod
    def _candidates(link, names, documents):
        name = link["target_name"]
        if not name:
            return [documents[link["source_id"]]] if link["source_id"] in documents else []
        if name.startswith("id:"):
            return [documents[name[3:]]] if name[3:] in documents else []
        return sorted(names.get(name, {}).values(), key=lambda doc: (doc["title"], doc["id"]))

    def _resolve_pending(self, db, space_id):
        names, documents = self._name_index(db, space_id)
        for link in db.execute(
            """SELECT * FROM document_links
            WHERE space_id=? AND target_id IS NULL AND bound_id IS NULL""",
            (space_id,),
        ).fetchall():
            candidates = self._candidates(link, names, documents)
            if len(candidates) == 1:
                target = candidates[0]["id"]
                db.execute(
                    "UPDATE document_links SET target_id=?,bound_id=? WHERE id=?",
                    (target, target, link["id"]),
                )

    @staticmethod
    def _index_links(db, document):
        previous = defaultdict(deque)
        for row in db.execute(
            "SELECT * FROM document_links WHERE source_id=? ORDER BY `start`", (document["id"],)
        ):
            previous[row["raw"]].append(row)
        db.execute("DELETE FROM document_links WHERE source_id=?", (document["id"],))
        for link in parse_links(document["content"]):
            old = previous[link["raw"]].popleft() if previous[link["raw"]] else None
            params = dict(
                link,
                id=old["id"] if old else None,
                space_id=document["space_id"],
                source_id=document["id"],
                target_id=old["target_id"] if old else None,
                bound_id=old["bound_id"] if old else None,
            )
            db.execute(
                """INSERT INTO document_links
                (id,space_id,source_id,target_id,bound_id,target_name,anchor,label,raw,`start`,`end`,excerpt)
                VALUES (:id,:space_id,:source_id,:target_id,:bound_id,:target_name,
                        :anchor,:label,:raw,:start,:end,:excerpt)""",
                params,
            )

    @staticmethod
    def _set_aliases(db, document_id, aliases):
        db.execute("DELETE FROM document_aliases WHERE document_id=?", (document_id,))
        db.executemany(
            "INSERT INTO document_aliases VALUES (?,?) ON DUPLICATE KEY UPDATE name=VALUES(name)",
            [(document_id, name) for name in aliases],
        )

    def relations(self, space_id, document_id, limit=200):
        with self.connection() as db:
            if not db.execute(
                "SELECT 1 FROM documents WHERE id=? AND space_id=?", (document_id, space_id)
            ).fetchone():
                return None
            names, documents = self._name_index(db, space_id)
            links = db.execute(
                """SELECT l.*,d.title source_title,t.title target_title
                FROM document_links l JOIN documents d ON d.id=l.source_id
                LEFT JOIN documents t ON t.id=l.target_id
                WHERE l.space_id=? AND l.source_id=? ORDER BY l.`start` LIMIT ?""",
                (space_id, document_id, limit + 1),
            ).fetchall()
            outgoing = []
            for row in links[:limit]:
                link = dict(row)
                link.pop("bound_id")
                candidates = self._candidates(row, names, documents)
                link["status"] = (
                    "resolved"
                    if row["target_id"]
                    else "broken"
                    if row["bound_id"]
                    else "ambiguous"
                    if len(candidates) > 1
                    else "unresolved"
                )
                link["candidates"] = candidates[:50] if link["status"] != "resolved" else []
                outgoing.append(link)
            rows = db.execute(
                """SELECT l.id,l.source_id,l.anchor,l.label,l.excerpt,
                    l.`start`,d.title source_title
                FROM document_links l JOIN documents d ON d.id=l.source_id
                WHERE l.space_id=? AND l.target_id=? AND d.space_id=?
                ORDER BY d.title,l.`start` LIMIT ?""",
                (space_id, document_id, space_id, limit + 1),
            ).fetchall()
            return {
                "outgoing": outgoing,
                "outgoing_truncated": len(links) > limit,
                "backlinks": [dict(row) for row in rows[:limit]],
                "backlinks_truncated": len(rows) > limit,
            }

    def bind_link(self, space_id, source_id, link_id, target_id):
        with self.connection() as db:
            # Both ends are checked; caller-provided IDs cannot cross knowledge bases.
            if not db.execute(
                """SELECT 1 FROM document_links
                WHERE id=? AND source_id=? AND space_id=?""",
                (link_id, source_id, space_id),
            ).fetchone():
                raise KeyError("链接不存在")
            if not db.execute(
                "SELECT 1 FROM documents WHERE id=? AND space_id=?", (target_id, space_id)
            ).fetchone():
                raise KeyError("目标文档不存在或不属于当前知识库")
            db.execute(
                "UPDATE document_links SET target_id=?,bound_id=? WHERE id=?",
                (target_id, target_id, link_id),
            )

    def graph(self, space_id, document_id, depth=1, limit=80):
        with self.connection() as db:
            if not db.execute(
                "SELECT 1 FROM documents WHERE id=? AND space_id=?", (document_id, space_id)
            ).fetchone():
                return None
            distances = {document_id: 0}
            frontier = [document_id]
            truncated = False
            for level in range(1, depth + 1):
                slots = ",".join("?" for _ in frontier)
                rows = db.execute(
                    f"""SELECT source_id,target_id FROM document_links
                    WHERE space_id=? AND target_id IS NOT NULL
                    AND (source_id IN ({slots}) OR target_id IN ({slots}))
                    GROUP BY source_id,target_id ORDER BY source_id,target_id LIMIT 501""",
                    (space_id, *frontier, *frontier),
                ).fetchall()
                truncated |= len(rows) > 500
                next_frontier = []
                for row in rows[:500]:
                    for node in (row["source_id"], row["target_id"]):
                        if node not in distances:
                            if len(distances) >= limit:
                                truncated = True
                            else:
                                distances[node] = level
                                next_frontier.append(node)
                frontier = next_frontier
                if not frontier:
                    break
            ids = list(distances)
            slots = ",".join("?" for _ in ids)
            nodes = [
                dict(r, depth=distances[r["id"]])
                for r in db.execute(
                    f"SELECT id,title FROM documents WHERE space_id=? AND id IN ({slots})",
                    (space_id, *ids),
                )
            ]
            edges = db.execute(
                f"""SELECT source_id source,target_id target,count(*) occurrences
                FROM document_links WHERE space_id=? AND source_id IN ({slots})
                AND target_id IN ({slots}) GROUP BY source_id,target_id
                ORDER BY source_id,target_id LIMIT 501""",
                (space_id, *ids, *ids),
            ).fetchall()
            return {
                "center_id": document_id,
                "nodes": nodes,
                "edges": [dict(r) for r in edges[:500]],
                "truncated": truncated or len(edges) > 500,
                "depth": depth,
            }
