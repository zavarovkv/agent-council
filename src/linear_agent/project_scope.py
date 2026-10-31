"""Shared project access, including sibling tasks created before project assignment."""


class ProjectScope:
    def __init__(self, issues, agent, registry, current):
        self.issues, self.agent, self.registry = issues, agent, registry
        self.cache = {current["id"]: current}
        self.project, self.root = self.anchor(current)
        self.team = current["team"]["id"]

    def get(self, reference):
        if reference not in self.cache:
            self.cache[reference] = self.issues.checked_issue(self.agent, self.registry, reference)
        return self.cache[reference]

    def anchor(self, issue):
        seen = set()
        for _ in range(16):
            if issue["id"] in seen:
                break
            seen.add(issue["id"])
            project = (issue.get("project") or {}).get("id")
            parent = (issue.get("parent") or {}).get("id")
            if project or not parent:
                return project, issue["id"]
            issue = self.get(parent)
        raise PermissionError("Invalid or oversized issue ancestry.")

    def require(self, issue):
        project, root = self.anchor(issue)
        if issue["team"]["id"] not in self.registry.teams or (
            project != self.project if self.project else project is not None or root != self.root
        ):
            raise PermissionError(
                "Use tasks and materials from the current project or parent task tree."
            )

    def filter(self):
        if self.project:
            return {"project": {"id": {"eq": self.project}}}
        # Unassigned task trees are a real work scope too; do not silently expand
        # them to the entire team merely because Linear projectId is null.
        pending, seen = [self.root], set()
        while pending:
            identity = pending.pop()
            if identity in seen:
                continue
            node = self.get(identity)
            try:
                self.require(node)
            except PermissionError:
                continue
            seen.add(identity)
            if len(seen) > 200:
                raise ValueError(
                    "Assign this large task tree to a Linear project before listing it."
                )
            children = node["children"]
            if children["pageInfo"]["hasNextPage"]:
                raise ValueError(
                    "Assign this large task tree to a Linear project before listing it."
                )
            pending.extend(row["id"] for row in children["nodes"])
        return {"id": {"in": sorted(seen)}}

    def fields(self, fields, *, creating=False):
        if creating:
            if self.project:
                fields.setdefault("projectId", self.project)
            else:
                fields.setdefault("parentId", self.root)
        if "projectId" in fields and fields["projectId"] != self.project:
            raise PermissionError("Keep shared work within the current project.")
        if "parentId" in fields:
            if fields["parentId"]:
                self.require(self.get(fields["parentId"]))
            elif not self.project:
                raise PermissionError("Keep this task in its authorized parent task tree.")
