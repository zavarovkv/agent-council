"""App-actor OAuth and the small, parameterized GraphQL surface this adapter needs."""

import hashlib
import re
import secrets
import threading
import time
from base64 import urlsafe_b64encode
from urllib.parse import unquote, urlencode, urlsplit

from .config import SCOPES, identifier
from .transport import RemoteError, request_json

API = "https://api.linear.app/graphql"
TOKEN = "https://api.linear.app/oauth/token"
SESSION_QUERY = """query CouncilSession($id:String!){agentSession(id:$id){
id appUser{id} creator{id} dismissedAt archivedAt status
issue{id title description url team{id} project{id} parent{id} delegate{id} creator{id}}
comment{body user{id}}}}"""
ACTIVITY_QUERY = """query CouncilActivity($id:String!){agentActivity(id:$id){
id agentSession{id} user{id} signal content{
... on AgentActivityPromptContent{type body}
... on AgentActivityThoughtContent{type body}
... on AgentActivityElicitationContent{type body}
... on AgentActivityResponseContent{type body}
... on AgentActivityErrorContent{type body}}}}"""
ISSUE_QUERY = """query CouncilIssue($id:String!){issue(id:$id){
id title description url team{id} parent{id} delegate{id} creator{id}}}"""


class Linear:
    def __init__(self, store, *, request=request_json, clock=time.time):
        self.store, self.request, self.clock = store, request, clock
        self.lock = threading.RLock()

    def authorization(self, registry, agent, browser):
        if self.store.installation(agent.slug):
            raise PermissionError("This app is already installed; preserve its existing grant.")
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        self.store.oauth_state(state, browser, agent.slug, verifier)
        challenge = (
            urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        )
        return "https://linear.app/oauth/authorize?" + urlencode(
            {
                "client_id": agent.client_id,
                "redirect_uri": registry.callback(agent.slug),
                "response_type": "code",
                "scope": ",".join(sorted(SCOPES)),
                "actor": "app",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )

    def exchange(self, agent, fields):
        value = self.request(
            TOKEN,
            method="POST",
            body=urlencode(
                {"client_id": agent.client_id, "client_secret": agent.client_secret, **fields}
            ).encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        for key in ("access_token", "refresh_token"):
            if (
                not isinstance(value.get(key), str)
                or not 1 <= len(value[key]) <= 8192
                or any(ord(char) < 33 or ord(char) > 126 for char in value[key])
            ):
                raise RemoteError()
        expires = value.get("expires_in")
        if (
            type(expires) not in {int, float}
            or not 30 <= expires <= 31 * 86400
            or str(value.get("token_type", "")).lower() != "bearer"
        ):
            raise RemoteError()
        if "scope" in value:
            scope = value["scope"]
            scopes = (
                frozenset(scope.replace(",", " ").split())
                if isinstance(scope, str)
                else frozenset(scope)
                if isinstance(scope, list)
                else frozenset()
            )
            if not SCOPES <= scopes:
                raise PermissionError("The application needs the configured agent scopes.")
        return value

    def callback(self, registry, agent, *, state, browser, code):
        verifier = self.store.consume_oauth(state, browser, agent.slug)
        token = self.exchange(
            agent,
            {
                "grant_type": "authorization_code",
                "redirect_uri": registry.callback(agent.slug),
                "code": code,
                "code_verifier": verifier,
            },
        )
        try:
            data = self.raw(
                token["access_token"], "query CouncilInstallation{viewer{id} organization{id}}", {}
            )
            app_user, organization = (
                identifier(data["viewer"]["id"]),
                identifier(data["organization"]["id"]),
            )
            if organization != registry.organization or app_user == registry.owner:
                raise PermissionError(
                    "Install the application in the explicitly configured workspace."
                )
            self.store.install(
                agent.slug,
                app_user,
                organization,
                token["access_token"],
                token["refresh_token"],
                self.clock() + token["expires_in"],
            )
        except Exception:
            try:
                self.request(
                    "https://api.linear.app/oauth/revoke",
                    method="POST",
                    body=urlencode(
                        {"token": token["refresh_token"], "token_type_hint": "refresh_token"}
                    ).encode(),
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            except Exception:
                pass
            raise

    def access(self, agent):
        with self.lock:
            current = self.store.installation(agent.slug)
            if not current:
                raise PermissionError("Install this Linear application first.")
            if current["expires"] <= self.clock() + 120:
                refreshed = self.exchange(
                    agent, {"grant_type": "refresh_token", "refresh_token": current["refresh"]}
                )
                self.store.install(
                    agent.slug,
                    current["app_user"],
                    current["organization"],
                    refreshed["access_token"],
                    refreshed["refresh_token"],
                    self.clock() + refreshed["expires_in"],
                )
                current = self.store.installation(agent.slug)
            return current["access"]

    def raw(self, access, query, variables):
        try:
            result = self.request(
                API,
                method="POST",
                body={"query": query, "variables": variables},
                headers={"Authorization": "Bearer " + access},
            )
        except RemoteError as error:
            if error.status in {401, 403}:
                raise PermissionError("Linear application access was revoked or denied.") from None
            raise
        if (
            not isinstance(result, dict)
            or result.get("errors")
            or not isinstance(result.get("data"), dict)
        ):
            raise RemoteError()
        return result["data"]

    def query(self, agent, query, variables):
        return self.raw(self.access(agent), query, variables)

    def session(self, agent, sid):
        return self.query(agent, SESSION_QUERY, {"id": identifier(sid)})["agentSession"]

    def activity(self, agent, aid):
        return self.query(agent, ACTIVITY_QUERY, {"id": identifier(aid)})["agentActivity"]

    def owner_mention(self, agent, registry):
        user = self.query(
            agent,
            "query CouncilQuestionOwner($id:String!){user(id:$id){id url active app isMentionable organization{id urlKey}}}",
            {"id": registry.owner},
        )["user"]
        if not isinstance(user, dict):
            raise PermissionError("The configured owner is unavailable.")
        organization = user.get("organization") or {}
        url = user.get("url")
        if (
            user.get("id") != registry.owner
            or user.get("active") is not True
            or user.get("app") is not False
            or user.get("isMentionable") is not True
            or not isinstance(organization, dict)
            or organization.get("id") != registry.organization
            or not isinstance(organization.get("urlKey"), str)
            or not isinstance(url, str)
            or len(url) > 512
        ):
            raise PermissionError(
                "Only the verified human owner may receive clarification requests."
            )
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.netloc != "linear.app"
            or parsed.query
            or parsed.fragment
            or not re.fullmatch(r"/[A-Za-z0-9_-]+/profiles/[A-Za-z0-9_.~%-]+", parsed.path)
            or not parsed.path.startswith("/" + organization.get("urlKey", "") + "/profiles/")
            or any(char.isspace() for char in unquote(parsed.path))
        ):
            raise PermissionError("Unrecognized owner profile URL.")
        return url

    def ensure_activity(self, agent, record):
        try:
            existing = self.activity(agent, record["id"])
        except RemoteError:
            existing = None
        if existing:
            if existing["agentSession"]["id"] != record["session"] or existing["content"] != {
                "type": record["kind"],
                "body": record["body"],
            }:
                raise PermissionError("Outbound activity identity conflict.")
            return
        # Caller-supplied UUIDs make a delayed first write and a retry mutually
        # exclusive in Linear, including a lost success response.
        result = self.query(
            agent,
            "mutation CouncilActivityCreate($input:AgentActivityCreateInput!){agentActivityCreate(input:$input){success agentActivity{id}}}",
            {
                "input": {
                    "id": record["id"],
                    "agentSessionId": record["session"],
                    "content": {"type": record["kind"], "body": record["body"]},
                }
            },
        )
        if (
            result["agentActivityCreate"].get("success") is not True
            or result["agentActivityCreate"]["agentActivity"]["id"] != record["id"]
        ):
            raise RemoteError()

    def issue(self, agent, issue):
        return self.query(agent, ISSUE_QUERY, {"id": identifier(issue)})["issue"]

    def workflow(self, agent, team):
        return self.query(
            agent,
            "query CouncilWorkflow($id:String!){team(id:$id){id name states{nodes{id name type position}} projects(first:100){nodes{id name} pageInfo{hasNextPage}} members(first:100){nodes{id name active app} pageInfo{hasNextPage}} labels(first:100){nodes{id name} pageInfo{hasNextPage}}}}",
            {"id": identifier(team)},
        )["team"]

    def issue_details(self, agent, issue):
        return self.query(
            agent,
            "query CouncilIssueDetails($id:String!){issue(id:$id){documents(first:50){nodes{id title url} pageInfo{hasNextPage endCursor}} attachments(first:50){nodes{id title url} pageInfo{hasNextPage endCursor}} id identifier title description url archivedAt priority dueDate team{id} creator{id} delegate{id} assignee{id} project{id} parent{id} state{id name type} labels{nodes{id name}} children(first:100){nodes{id identifier state{id type}} pageInfo{hasNextPage}} comments(last:10){nodes{id body user{id name}}}}}",
            {"id": issue},
        )["issue"]

    def issues(self, agent, teams, *, query="", cursor=None, scope=None):
        filters = {"team": {"id": {"in": sorted(teams)}}}
        if scope:
            filters.update(scope)
        if query:
            filters["title"] = {"containsIgnoreCase": query}
        return self.query(
            agent,
            "query CouncilIssues($filter:IssueFilter!,$after:String){issues(filter:$filter,first:25,after:$after){nodes{id identifier title url priority team{id} state{id name type} assignee{id} delegate{id} project{id}} pageInfo{hasNextPage endCursor}}}",
            {"filter": filters, "after": cursor},
        )["issues"]

    def issue_comments(self, agent, issue, cursor):
        return self.query(
            agent,
            "query CouncilComments($id:String!,$after:String){issue(id:$id){comments(first:1,after:$after){nodes{id body user{id name}} pageInfo{hasNextPage endCursor}}}}",
            {"id": identifier(issue), "after": cursor},
        )["issue"]["comments"]

    def project_materials(self, agent, project):
        return self.query(
            agent,
            "query CouncilProject($id:String!){project(id:$id){id name url description content documents(first:50){nodes{id title url} pageInfo{hasNextPage endCursor}} attachments(first:50){nodes{id title url} pageInfo{hasNextPage endCursor}}}}",
            {"id": identifier(project)},
        )["project"]

    def document(self, agent, document):
        return self.query(
            agent,
            "query CouncilDocument($id:String!){document(id:$id){id title url content project{id} issue{id}}}",
            {"id": identifier(document)},
        )["document"]

    def save_document(self, agent, identity, fields, *, create=False):
        operation = "documentCreate" if create else "documentUpdate"
        query = (
            "mutation CouncilDocumentCreate($input:DocumentCreateInput!){documentCreate(input:$input){success document{id}}}"
            if create
            else "mutation CouncilDocumentUpdate($id:String!,$input:DocumentUpdateInput!){documentUpdate(id:$id,input:$input){success document{id}}}"
        )
        variables = (
            {"input": {"id": identity, **fields}} if create else {"id": identity, "input": fields}
        )
        result = self.query(agent, query, variables)[operation]
        if result.get("success") is not True or result["document"]["id"] != identity:
            raise RemoteError()

    def create_issue(self, agent, fields):
        result = self.query(
            agent,
            "mutation CouncilIssueCreate($input:IssueCreateInput!){issueCreate(input:$input){success issue{id}}}",
            {"input": fields},
        )["issueCreate"]
        if result.get("success") is not True or result["issue"]["id"] != fields["id"]:
            raise RemoteError()

    def update_issue(self, agent, issue, fields):
        result = self.query(
            agent,
            "mutation CouncilIssueUpdate($id:String!,$input:IssueUpdateInput!){issueUpdate(id:$id,input:$input){success issue{id}}}",
            {"id": identifier(issue), "input": fields},
        )["issueUpdate"]
        if result.get("success") is not True or result["issue"]["id"] != issue:
            raise RemoteError()

    def comment(self, agent, identity):
        return self.query(
            agent,
            "query CouncilComment($id:String!){comment(id:$id){id body issue{id}}}",
            {"id": identifier(identity)},
        )["comment"]

    def create_comment(self, agent, identity, issue, body):
        result = self.query(
            agent,
            "mutation CouncilCommentCreate($input:CommentCreateInput!){commentCreate(input:$input){success comment{id}}}",
            {"input": {"id": identity, "issueId": issue, "body": body}},
        )["commentCreate"]
        if result.get("success") is not True or result["comment"]["id"] != identity:
            raise RemoteError()

    def ensure_child(self, agent, child, *, parent_issue, team, delegate):
        try:
            existing = self.issue(agent, child["issue"])
        except RemoteError:
            existing = None
        if existing:
            if (
                (existing.get("parent") or {}).get("id") != parent_issue
                or (existing.get("delegate") or {}).get("id") != delegate
                or existing["team"]["id"] != team
            ):
                raise PermissionError("Sub-issue identity conflict.")
            return existing
        result = self.query(
            agent,
            "mutation CouncilSubIssue($input:IssueCreateInput!){issueCreate(input:$input){success issue{id title url}}}",
            {
                "input": {
                    "id": child["issue"],
                    "parentId": parent_issue,
                    "teamId": team,
                    "delegateId": delegate,
                    "title": child["title"],
                    "description": child["description"],
                }
            },
        )
        if (
            result["issueCreate"].get("success") is not True
            or result["issueCreate"]["issue"]["id"] != child["issue"]
        ):
            raise RemoteError()
        return result["issueCreate"]["issue"]
