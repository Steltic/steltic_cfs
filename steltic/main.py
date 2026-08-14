"""FastAPI app: vanilla frontend + the streamed agent run + report serving.

TWO POSTURES, one file (Cloud Run wrapper ported from the AISC steel_by_webpage app, 2026-08-12):

* **Local / self-host (DEMO=0)** -- the historical single-user app: no login, no Turnstile, no
  control plane. An implicit "local" session is created on first touch, so jobs stay under
  sessions/local/jobs exactly as before. Bind to 127.0.0.1 (the CLI default).
* **Cloud demo (DEMO=1)** -- the public posture: the /api/enter door (Cloudflare Turnstile +
  consent) starts a session; the user's LLM key lives per-session in server memory
  (auth.SESSION_CREDS), never on disk; every limit keys off the anonymous visitor seat; the
  control plane on the RAG VM provides fleet-wide stop, one-run-per-visitor, daily caps and the
  monthly run-hour budget; diskcap sweeps tmpfs; usage telemetry is metadata-only.

The steel engine + model-written code run ONLY in the sandbox executor (uid-drop when
SANDBOX_UID is set)."""
import base64, io, json, os, posixpath, secrets, time, zipfile
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.middleware.sessions import SessionMiddleware
from . import config, agent, demo, auth, gate, usage, ctl, turnstile, diskcap
from .job_tools import JobWorkspace, _clean_name
from .sandbox import make_executor

app = FastAPI(title="Steltic CFS")

_secret = config.SECRET_KEY or "dev-insecure-key-change-me"
if not config.SECRET_KEY and config.DEMO_MODE:
    print("[warn] SECRET_KEY not set -- using an insecure dev key. Set SECRET_KEY in production.")
app.add_middleware(SessionMiddleware, secret_key=_secret, same_site="lax",
                   https_only=False, max_age=config.SESSION_HOURS * 3600)


# Frontend freshness: no-cache = store but REVALIDATE via ETag each load, so deploys arrive.
@app.middleware("http")
async def _no_stale_frontend(request, call_next):
    resp = await call_next(request)
    p = request.url.path
    if p == "/" or p.startswith("/static/") or p.startswith("/api/report/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp

EXECUTOR = make_executor()

# Startup posture, printed loudly because each line can silently cost money or leak.
if config.DEMO_MODE:
    print(f"[init] DEMO mode: examples only, brief locked, uploads off, "
          f"{config.DEMO_MAX_RUN_SEC // 60} min hard cap per design")
    if not turnstile.enabled():
        print("[init] WARNING: TURNSTILE_SECRET unset -- the demo door is OPEN. The ctl_plane "
              "monthly run-hour budget is the only thing bounding cost.")
else:
    print("[init] local mode (not DEMO): user briefs, uploads and session restore are ENABLED")
from .sandbox.subprocess_exec import privilege_drop_active as _drop_active
if EXECUTOR.name == "subprocess" and config.DEMO_MODE and not _drop_active():
    print("[init] WARNING: run_python executes as the APP user. Set SANDBOX_UID on any deployment "
          "that serves more than one visitor -- otherwise model code can read co-tenants' API keys.")
usage.install_lifecycle()   # privacy-safe telemetry: instance boot/stop markers (metadata only)


# ---------------- sessions: demo door vs implicit local session ----------------
def _ensure_local_session(request: Request):
    """DEMO=0: the single-user app needs no door -- materialize a stable 'local' session."""
    if not request.session.get("user"):
        request.session["user"] = "local"
        request.session["sid"] = "local"                     # keeps jobs under sessions/local/jobs
        request.session["ts"] = int(time.time())
        request.session["consented"] = True


def current_user(request: Request) -> str:
    if not config.DEMO_MODE:
        _ensure_local_session(request)
        return "local"
    return auth.current_user(request)


def get_sid(request: Request) -> str:
    if not config.DEMO_MODE:
        _ensure_local_session(request)
        return "local"
    return auth.get_sid(request)


def _sse(ev: dict) -> str:
    return "data: " + json.dumps(ev) + "\n\n"


def _clean_images(raw, max_files: int = 3, max_bytes: int = 5 * 1024 * 1024) -> list:
    """Validate optional base64 data-URL images from the client: keep at most `max_files`, each whose
    decoded size is <= `max_bytes`. Returns [{name, type, data_url}]. Anything malformed is dropped."""
    out = []
    if not isinstance(raw, list):
        return out
    for it in raw:
        if len(out) >= max_files:
            break
        if not isinstance(it, dict):
            continue
        url = it.get("data_url") or ""
        if not isinstance(url, str) or not url.startswith("data:image/") or "," not in url:
            continue
        approx = (len(url.split(",", 1)[1]) * 3) // 4      # base64 -> bytes, no need to actually decode
        if approx > max_bytes:
            continue
        out.append({"name": str(it.get("name") or "image")[:120],
                    "type": str(it.get("type") or "image/*")[:60], "data_url": url})
    return out


def _restore_zip_into(base, data: bytes):
    """Validate + unpack a unified-zip's resumable state into the job dir `base`. Confined to `base`
    (zip-slip guarded), whitelisted paths only, conversation.json must parse as JSON. Returns
    (files_written, had_conversation). Raises ValueError on an invalid archive / bad conversation.json.
    Shared by /api/restore and the resume path of /api/run."""
    base = base.resolve()
    if not data or len(data) > 80 * 1024 * 1024:
        raise ValueError("empty or oversized archive (max 80 MB)")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception:
        raise ValueError("not a valid zip archive")
    infos = [i for i in zf.infolist() if not i.is_dir()]
    if len(infos) > 1000:
        raise ValueError("too many files in archive")
    if sum(i.file_size for i in infos) > 200 * 1024 * 1024:
        raise ValueError("archive expands too large")

    def _allowed(rel: str) -> bool:
        if rel in ("conversation.json", "run_log.jsonl", "activity_log.jsonl", "cfg.py"):
            return True
        if "/" not in rel and rel.endswith(".py"):                    # model_opensees.py, model_static.py, ...
            return True
        if "/" not in rel and rel.startswith("report") and rel.endswith(".html"):
            return True
        return rel.split("/", 1)[0] in ("design", "rag", "figs")      # subtree files only

    base.mkdir(parents=True, exist_ok=True)
    written, had_conv = 0, False
    for i in infos:
        name = i.filename.replace("\\", "/")
        rel = name.split("/", 1)[1] if "/" in name else name          # strip the <building>/ archive root
        if not rel:
            continue
        rel = posixpath.normpath(rel)
        if rel.startswith("/") or rel.startswith("..") or ".." in rel.split("/"):
            continue                                                  # path-traversal guard
        if not _allowed(rel) or i.file_size > 60 * 1024 * 1024:
            continue
        target = (base / rel).resolve()
        if base not in target.parents:                                # zip-slip guard (must stay under the job dir)
            continue
        payload = zf.read(i)
        if rel == "conversation.json":
            try:
                json.loads(payload.decode("utf-8"))                   # must be valid JSON or we refuse the whole restore
            except Exception:
                raise ValueError("conversation.json in the archive is not valid JSON")
            had_conv = True
        if target.is_dir():
            continue                                                  # never clobber a directory
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.parent / (target.name + ".tmp~")
        tmp.write_bytes(payload)
        os.replace(tmp, target)                                       # ATOMIC
        written += 1
    return written, had_conv


# ---------------- entry ----------------
@app.post("/api/enter")
async def enter(request: Request):
    """Open-access Demo entry: verify the Turnstile token + consent, then start a session. All
    limits downstream key off the anonymous visitor seat (auth.demo_seat) -- the raw IP is never
    stored. Locally (DEMO=0) this is a no-op door that opens the implicit session."""
    if not config.DEMO_MODE:
        _ensure_local_session(request)
        return {"ok": True, "user": "local", "demo": False}
    body = await request.json()
    ip = auth.client_ip(request)
    ok, why = turnstile.verify((body.get("turnstile") or "").strip(), ip)
    if not ok:
        usage._emit("enter_denied", reason=why)          # telemetry counter: the IP is NEVER logged
        raise HTTPException(403, "verification failed -- please reload the page and try again")
    if not body.get("consented"):
        raise HTTPException(403, "please accept the Terms to continue")
    user = auth.start_demo_session(request, ip)
    request.session["consented"] = True   # accepted at the door -> rides in the signed cookie
    usage.seat_signin(user)
    return {"ok": True, "user": user, "demo": True}


@app.get("/api/config")
async def app_config():
    """Frontend bootstrap (pre-session): demo flag, the server-driven example catalog, the
    Turnstile site key, and the demo budget status."""
    out = {"demo": config.DEMO_MODE,
           "examples": [{"key": k, "label": lbl} for k, lbl in demo.EXAMPLE_CHOICES],
           "turnstile_site_key": turnstile.site_key(),
           "terms_version": config.TERMS_VERSION,
           "demo_max_run_sec": config.DEMO_MAX_RUN_SEC,
           "run_soft_deadline_sec": config.RUN_SOFT_DEADLINE_SEC}
    if config.DEMO_MODE:
        out.update(demo.METER.status())
    return out


@app.post("/api/logout")
async def logout(request: Request):
    auth.logout_session(request)
    return {"ok": True}


@app.get("/api/me")
async def me(request: Request):
    if config.DEMO_MODE and not request.session.get("user"):
        return {"user": None, "executor": EXECUTOR.name, "demo": True}
    user = current_user(request)
    sid = get_sid(request)
    creds = auth.get_creds(sid)
    return {"user": user, "executor": EXECUTOR.name, "has_creds": bool(creds),
            "demo": config.DEMO_MODE,
            "model": (creds or {}).get("model"), "base_url": (creds or {}).get("base_url"),
            "reasoning": (creds or {}).get("reasoning", "high"),
            "max_tokens": (creds or {}).get("max_tokens", 32000),
            "provider": (creds or {}).get("provider", ""),
            "consented": bool(request.session.get("consented")),
            "terms_version": config.TERMS_VERSION,
            "run_soft_deadline_sec": config.RUN_SOFT_DEADLINE_SEC,
            "demo_max_run_sec": config.DEMO_MAX_RUN_SEC,
            "examples": ([{"key": k, "label": lbl} for k, lbl in demo.EXAMPLE_CHOICES]
                         if config.DEMO_MODE else None)}


@app.post("/api/creds")
async def set_creds(request: Request, user: str = Depends(current_user)):
    body = await request.json()
    base = (body.get("base_url") or "").strip()
    key = (body.get("api_key") or "").strip()
    model = (body.get("model") or "").strip()
    reasoning = (body.get("reasoning") or "high").strip()
    provider = (body.get("provider") or "").strip()
    try: max_tokens = max(256, min(int(body.get("max_tokens") or 32000), 200000))
    except Exception: max_tokens = 32000
    if model != "MOCK":
        if not base.startswith(("http://", "https://")):
            raise HTTPException(400, "base_url must start with http:// or https://")
        if not key:
            raise HTTPException(400, "api_key required")
        if not model:
            raise HTTPException(400, "model required")
    auth.set_creds(get_sid(request), base, key, model, reasoning, max_tokens, provider)
    return {"ok": True}


EXAMPLE_BRIEFS = {
    "ex1": "CFS_Ex1_ShearWall_4levels.txt",
    "ex2": "CFS_Ex2_StrapBraced_6levels.txt",
    "ex3": "CFS_Ex3_BearingWall_8levels_podium.txt",
    "ex4": "CFS_Ex4_SBSW_5levels_podium.txt",
    "ex5": "CFS_Ex5_StrapBraced_6levels.txt",
    "ex6": "CFS_Ex6_WSP_5levels_Lplan.txt",
    "ex7": "CFS_Ex7_SteelSheet_6levels_Zplan.txt",
    "ex8": "CFS_Ex8_StrapBraced_4levels_Tplan.txt",
    "ex9": "CFS_Ex9_Podium_5over2_Uplan.txt",
    "ex10": "CFS_Ex10_SBMF_2levels_highbay.txt",
    "ex11": "CFS_Ex11_Gypsum_3levels_coastal_wind.txt",
    "ex12": "CFS_Ex12_SteelSheet_8levels_crossplan.txt",
    "ex13": "CFS_Ex13_TypeII_perforated_4levels.txt",
    "ex14": "CFS_Ex14_Mixed_3levels_splitlevel.txt",
    "ex15": "CFS_Ex15_WSP_6levels_steppedbar.txt",
    "ex16": "CFS_Ex16_Portal_singlespan_warehouse.txt",
    "ex17": "CFS_Ex17_Portal_twospan_snow.txt",
    "ex18": "CFS_Ex18_Portal_mezzanine_mixedR.txt",
    "ex19": "CFS_Ex19_Portal_monorail_fatigue.txt",
    # Ex20-24 (storage racks) DESCOPED 2026-07-30 -- deliberately absent
    "ex25": "CFS_Ex25_PurlinGirt_uplift_retrofit.txt",
    "ex26": "CFS_Ex26_Canopy_open_wind.txt",
    "ex27": "CFS_Ex27_Portal_singlechannel_torsion.txt",
    "ex28": "CFS_Ex28_AgBuilding_partially_enclosed.txt",
    "ex29": "CFS_Ex29_Mezzanine_freestanding_existing.txt",
    "ex30": "CFS_Ex30_ColdStorage_portal.txt",
    # legacy aliases (old UI buttons / bookmarks)
    "design": "CFS_Ex1_ShearWall_4levels.txt",
    "redesign": "CFS_Ex1_ShearWall_4levels.txt",
}


@app.get("/api/example/{which}")
async def example_brief(which: str, user: str = Depends(current_user)):
    if config.DEMO_MODE and which not in demo.DEMO_KEYS:
        raise HTTPException(404, "unknown example")             # aliases rejected in demo
    fn = EXAMPLE_BRIEFS.get(which)
    if not fn:
        raise HTTPException(404, "unknown example")
    p = config.EXAMPLES_DIR / fn
    try:
        return {"brief": p.read_text(encoding="utf-8", errors="replace")}
    except Exception as e:
        raise HTTPException(404, f"example not available: {e}")


def _demo_brief(body) -> tuple[str, str]:
    """DEMO branch of /api/run: resolve the example KEY to (building, brief text).
    The client's brief/images/building/fidelity are IGNORED ENTIRELY -- 'you cannot
    enter your own data' is enforced here in Python, not in the UI."""
    key = str(body.get("example") or "").strip().lower()
    if key not in demo.DEMO_KEYS:
        raise HTTPException(400, "Demo runs example briefs only -- pick one from the list")
    p = config.EXAMPLES_DIR / EXAMPLE_BRIEFS[key]
    try:
        return demo.demo_building(key), p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        raise HTTPException(404, "example brief unavailable on this install")


# ---------------- run (SSE) ----------------
@app.post("/api/run")
async def run(request: Request, user: str = Depends(current_user)):
    body = await request.json()
    resume = bool(body.get("resume"))
    if config.DEMO_MODE:
        building, brief = _demo_brief(body)
        if resume:
            brief = ""                       # continues ride the saved conversation
        images = []                          # no client data in demo mode
        # analysis fidelity PINNED to auto: the agent follows preflight_fidelity()'s recommendation.
    else:
        building = _clean_name(body.get("building") or "Building")
        brief = (body.get("brief") or "").strip()
        fid = str(body.get("analysis_fidelity") or "auto").strip().lower()
        if brief and fid in ("0", "1", "2"):
            brief += ("\n\nUSER-SELECTED ANALYSIS FIDELITY: Tier %s (from the UI selector). "
                      "Set cfg['analysis_fidelity']=%s; if the preflight warns the tier is "
                      "below the floor for this structure kind, STOP and tell the user why "
                      "before proceeding (see frontend/analysis_fidelity_explainer.md)."
                      % (fid, fid))
        images = _clean_images(body.get("images"))
    if not brief and not resume:
        raise HTTPException(400, "brief required")
    sid = get_sid(request)
    creds = auth.get_creds(sid)
    if not creds:
        raise HTTPException(400, "set your LLM base-url + API key in Settings first")
    if config.DEMO_MODE and not request.session.get("consented"):
        raise HTTPException(403, "please accept the Terms at the door (reload the page and re-enter)")
    run_key = f"{sid}:{building}"
    cc = ctl.cfg()
    if cc.get("maintenance"):
        raise HTTPException(503, cc.get("banner") or
                            "Steltic is briefly down for maintenance -- please try again shortly.")
    gate.request_cancel(run_key); gate.quota_release(run_key)   # supersede any prior/leaked run of THIS project
    q = gate.quota_check(user)
    if q:
        raise HTTPException(429, q)
    jobs_dir = config.SESSIONS_DIR / sid / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    # /tmp is TMPFS on Cloud Run -- job files are RAM, shared with co-tenants. Sweep before the run.
    diskcap.sweep(config.SESSIONS_DIR / sid, keep=building)
    job_dir = jobs_dir / building
    # A fresh (non-resume) run ARCHIVES any existing same-name job instead of overwriting it, so previous
    # work is never auto-cleared. Resume keeps the folder as-is. Different project names are untouched.
    if not resume and job_dir.exists() and any(job_dir.iterdir()):
        import datetime
        try: job_dir.rename(jobs_dir / (building + "__" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
        except Exception: pass
    # Resume from a browser-held snapshot (restore + resume ride one request; only fills a missing
    # conversation). DEMO: the snapshot path writes client bytes to disk = an upload vector -> disabled.
    snapshot_b64 = None if config.DEMO_MODE else body.get("snapshot_b64")
    if resume and snapshot_b64 and not (job_dir / "conversation.json").exists():
        try:
            _restore_zip_into(job_dir, base64.b64decode(snapshot_b64))
        except Exception:
            pass
    if resume and not (job_dir / "conversation.json").exists():
        raise HTTPException(409, "nothing to resume for '%s', and your browser holds no saved copy of it. "
                            "Load the project's .zip via 'Restore session…' and press Continue again, or "
                            "start over with Design building." % building)
    allowed, info = ctl.run_acquire(user, building, fresh=not resume)   # one live run per visitor (fleet-wide)
    ctl.cancel_clear_local(user)                                        # a continue is free
    if not allowed:
        reason = (info or {}).get("reason")
        if reason == "at_capacity":
            raise HTTPException(429, "The free Demo has reached its capacity for this month. "
                                "Steltic CFS is open source -- run it locally with no limits: "
                                "github.com/Steltic/steltic_cfs")
        if reason == "daily_limit":
            raise HTTPException(429, "You've used your %s Demo runs for today. Try again tomorrow, "
                                "or run Steltic CFS locally with no limits: github.com/Steltic/steltic_cfs"
                                % (info or {}).get("max_per_day", 3))
        raise HTTPException(429, "you already have a run in progress (%s, started %s min ago) -- "
                            "stop it or wait for it to finish; one run at a time."
                            % ((info or {}).get("building", "?"), (info or {}).get("started_min_ago", "?")))
    ws = JobWorkspace(jobs_dir)
    gate.quota_acquire(user, run_key)
    log_path = jobs_dir / building / "run_log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # DEMO: the per-run hard cap rides the agent's CANCEL flag (not a loop break), so the save
    # handlers run and the visitor still gets a complete package at expiry. Two clocks back it:
    # the local METER (works standalone) and the ctl-plane heartbeat (fleet-wide, survives the
    # run moving between instances on a Continue).
    if config.DEMO_MODE:
        demo.METER.start(building, count_run=not resume)

    def gen():
        _last_bundle = [time.time()]
        _last_hb = [time.time()]
        _ckpt_sec = ctl.cfg().get("checkpoint_bundle_sec") or config.CHECKPOINT_BUNDLE_SEC
        _t0 = time.time()
        _stats = {"outcome": "disconnected", "tin": 0, "tout": 0, "steps": 0, "errors": 0, "pauses": 0}
        _demo_expired = [False]
        _sweeper = diskcap.PeriodicSweeper(config.SESSIONS_DIR / sid, keep=building)
        usage.run_start(user, building, model=creds.get("model"), provider=creds.get("provider"),
                        reasoning=creds.get("reasoning"), resume=resume)
        _cancel = lambda: (gate.is_cancelled(run_key)
                           or _demo_expired[0]
                           or (config.DEMO_MODE and demo.METER.expired(building))
                           or ctl.cancel_check(user))
        try:
            for ev in agent.run_design(ws, EXECUTOR, creds["base_url"], creds["api_key"],
                                       creds["model"], building, brief,
                                       max_tok=creds.get("max_tokens", 32000),
                                       reasoning=creds.get("reasoning", "high"),
                                       provider=creds.get("provider", ""), resume=resume,
                                       images=images,
                                       cancel=_cancel):
                if config.DEMO_MODE:
                    demo.METER.tick(building)      # abandoned runs still bill
                try:
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(json.dumps(ev) + "\n")
                except Exception:
                    pass
                yield _sse(ev)
                _t = ev.get("type") if isinstance(ev, dict) else None
                if _t == "usage":
                    _stats["tin"] = ev.get("cum_in", _stats["tin"]); _stats["tout"] = ev.get("cum_out", _stats["tout"])
                elif _t == "tool":
                    _stats["steps"] = max(_stats["steps"], int(ev.get("step") or 0))
                elif _t == "error":
                    _stats["errors"] += 1
                elif _t == "paused":
                    _stats["pauses"] += 1
                    _stats["outcome"] = "stopped" if "stopped" in str(ev.get("reason", "")) else "paused"
                elif _t == "done":
                    _stats["outcome"] = "done"
                if time.time() - _last_hb[0] > 30:
                    _last_hb[0] = time.time()
                    _hb = ctl.run_hb(user)     # keeps the seat alive AND meters the monthly budget
                    _sweeper.maybe()           # tmpfs is RAM and shared -- keep this session in budget
                    if config.DEMO_MODE and not _demo_expired[0]:
                        _elapsed = int((_hb or {}).get("elapsed_sec") or 0)
                        _cap = int((_hb or {}).get("max_run_sec") or config.DEMO_MAX_RUN_SEC)
                        if _elapsed and _elapsed >= _cap:
                            _demo_expired[0] = True
                            _stats["outcome"] = "demo_limit"
                            yield _sse({"type": "demo_limit", "elapsed_sec": _elapsed,
                                        "max_run_sec": _cap,
                                        "text": "Demo time limit reached (%d minutes). Wrapping up — "
                                                "your design package is ready to download below."
                                                % (_cap // 60)})
                _ckpt = (_t == "tool_result" and _ckpt_sec
                         and time.time() - _last_bundle[0] > _ckpt_sec)
                # 'error' included: a run that dies (e.g. LLM retries exhausted) still ships
                # whatever files exist -- same visitor-keeps-their-work guarantee.
                if _t in ("done", "paused", "error") or _ckpt:
                    try:                                   # ship the whole bundle over THIS SSE connection (the same
                        from .bundle import make_zip       # instance that holds the files) so the browser captures it
                        _zip = make_zip(job_dir, building)
                        if _zip and len(_zip) <= 30 * 1024 * 1024:
                            _last_bundle[0] = time.time()
                            yield _sse({"type": "bundle", "building": building,
                                        "zip_b64": base64.b64encode(_zip).decode("ascii")})
                    except Exception:
                        pass
        except Exception as e:
            _stats["outcome"] = "server_error"
            yield _sse({"type": "error", "text": f"{type(e).__name__}: {e}"})
        finally:
            gate.quota_release(run_key)
            ctl.run_release(user)
            if config.DEMO_MODE:
                demo.METER.finish(building)
            usage.run_end(user, building, _stats["outcome"], time.time() - _t0,
                          tokens_in=_stats["tin"], tokens_out=_stats["tout"], steps=_stats["steps"],
                          errors=_stats["errors"], pauses=_stats["pauses"])

    sync_gen = gen()

    async def agen():
        # Disconnect = Stop. If the browser closes the SSE stream, the running generator must be
        # closed from here: that injects GeneratorExit at its suspended yield, aborts any in-flight
        # provider call (no runaway token spend), and runs the loop's save handlers.
        from starlette.concurrency import iterate_in_threadpool, run_in_threadpool
        try:
            async for chunk in iterate_in_threadpool(sync_gen):
                yield chunk
                if await request.is_disconnected():
                    break
        finally:
            gate.request_cancel(run_key)
            try:
                # SHIELDED: on the cancellation path every await in this scope is itself cancelled --
                # unshielded, close() never runs and the loop stays suspended holding an OPEN provider
                # stream. The shield guarantees the GeneratorExit injection happens.
                import anyio
                with anyio.CancelScope(shield=True):
                    await run_in_threadpool(sync_gen.close)
            except BaseException:
                pass

    return StreamingResponse(agen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/stop")
async def stop(request: Request, user: str = Depends(current_user)):
    """Stop an in-progress run: signal the loop to halt at its next step AND free the concurrency
    slot immediately. The ctl-plane flag is fleet-wide -- the RUNNING instance polls it every
    ~2.5 s, so Stop works even when this POST lands on a different instance."""
    body = await request.json()
    building = _clean_name(body.get("building") or "")
    run_key = f"{get_sid(request)}:{building}"
    was_active = gate.request_cancel(run_key)
    gate.quota_release(run_key)
    flagged = ctl.cancel_set(user)
    return {"ok": True, "stopped": building, "was_active": was_active, "fleet_flag": flagged}


# ---------------- report serving (confined to the caller's session jobs dir) ----------------
@app.get("/api/report/{building}/{path:path}")
async def report_file(request: Request, building: str, path: str = "report.html",
                      user: str = Depends(current_user)):
    base = (config.SESSIONS_DIR / get_sid(request) / "jobs" / _clean_name(building)).resolve()
    target = (base / (path or "report.html")).resolve()
    if not (target == base or base in target.parents):
        raise HTTPException(403, "path escapes the job folder")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(str(target))


@app.get("/api/report/{building}")
async def report_root(request: Request, building: str, user: str = Depends(current_user)):
    return await report_file(request, building, "report.html", user)


@app.get("/api/download/{building}")
async def download_bundle(request: Request, building: str, user: str = Depends(current_user)):
    """Offline bundle: one self-contained report.html (figures inlined) + the agent's model script(s)
    + calc_package.json, zipped. Confined to the caller's own session jobs/ folder."""
    b = _clean_name(building)
    base = (config.SESSIONS_DIR / get_sid(request) / "jobs").resolve()
    job_dir = (base / b).resolve()
    if base not in job_dir.parents:
        raise HTTPException(403, "path escapes the jobs folder")
    if not ((job_dir / "report.html").exists() or (job_dir / "conversation.json").exists()):
        raise HTTPException(404, "nothing to download yet")
    from .bundle import make_zip
    data = make_zip(job_dir, b)
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{b}_report.zip"'})


@app.post("/api/restore/{building}")
async def restore_session(request: Request, building: str, user: str = Depends(current_user)):
    """Rehydrate a job folder from the unified zip the browser holds (or the user re-uploads).
    Refused in DEMO mode: restoring an archive is a file upload, and the Demo accepts none."""
    if config.DEMO_MODE:
        raise HTTPException(403, "Disabled for Demo version")
    b = _clean_name(building)
    base = (config.SESSIONS_DIR / get_sid(request) / "jobs" / b).resolve()
    data = await request.body()
    try:
        written, had_conv = _restore_zip_into(base, data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "restored": written, "has_conversation": had_conv, "building": b}


@app.post("/api/consent")
async def consent(request: Request, user: str = Depends(current_user)):
    gate.record_consent(user)
    return {"ok": True, "version": config.TERMS_VERSION}


@app.get("/api/terms")
async def terms_api():
    p = config.DISCLAIMER_FILE
    return {"version": config.TERMS_VERSION,
            "markdown": p.read_text(encoding="utf-8") if p.exists() else "Terms unavailable."}


@app.get("/terms")
async def terms_page():
    p = config.DISCLAIMER_FILE
    txt = p.read_text(encoding="utf-8") if p.exists() else "Disclaimer unavailable."
    return Response(txt, media_type="text/plain; charset=utf-8")


@app.get("/privacy")
async def privacy_page():
    p = config.PRIVACY_FILE
    txt = p.read_text(encoding="utf-8") if p.exists() else "Privacy policy unavailable."
    return Response(txt, media_type="text/plain; charset=utf-8")


@app.api_route("/api/ping", methods=["GET", "POST", "HEAD"])
async def ping():
    # Keepalive target: the frontend pings while the user is active so Cloud Run keeps THIS
    # (session-affinity) instance warm -> job files survive the gap between a run and a download.
    return {"ok": True}


@app.get("/api/log/{building}")
async def get_log(request: Request, building: str, user: str = Depends(current_user)):
    base = config.SESSIONS_DIR / get_sid(request) / "jobs" / _clean_name(building)
    events = []
    p = base / "run_log.jsonl"
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip():
                try: events.append(json.loads(line))
                except Exception: pass
    return {"events": events, "resumable": (base / "conversation.json").exists(),
            "has_report": (base / "report.html").exists()}


@app.get("/healthz")
async def healthz():
    return {"ok": True, "executor": EXECUTOR.name}


# RAG reachability probe (uptime checks). Exercises the REAL path runs depend on
# (Cloud Run -> VPC connector -> VM :8080 -> embed -> Qdrant) with a tiny top_k=1 query.
_rag_health = {"t": -1e9, "up": False, "ms": 0}

@app.get("/healthz/rag")
async def healthz_rag():
    import asyncio, urllib.request
    from fastapi.responses import JSONResponse
    now = time.monotonic()
    if now - _rag_health["t"] > config.RAG_HEALTH_CACHE_SEC:
        _rag_health["t"] = now                      # set first: concurrent probes collapse to one window
        t0 = time.monotonic()
        try:
            if not config.RAG_API_URL:
                raise RuntimeError("RAG_API_URL not set")
            def _probe():
                body = json.dumps({"query": "E2 compression", "collection": "engineering_standards_S100",
                                   "top_k": 1}).encode()
                hdrs = {"Content-Type": "application/json"}
                if config.RAG_API_TOKEN:
                    hdrs["Authorization"] = "Bearer " + config.RAG_API_TOKEN
                req = urllib.request.Request(config.RAG_API_URL, data=body, headers=hdrs)
                with urllib.request.urlopen(req, timeout=8) as r:
                    json.loads(r.read())
            await asyncio.to_thread(_probe)
            _rag_health.update(up=True, ms=int((time.monotonic() - t0) * 1000))
        except Exception:
            _rag_health.update(up=False, ms=0)
    return JSONResponse({"rag": "up" if _rag_health["up"] else "down", "probe_ms": _rag_health["ms"]},
                        status_code=200 if _rag_health["up"] else 503)


# ---------------- static frontend ----------------
app.mount("/static", StaticFiles(directory=str(config.FRONTEND_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(config.FRONTEND_DIR / "index.html"))
