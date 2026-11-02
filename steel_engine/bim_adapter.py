"""CFS physical-layout capture using native portal and wall declarations."""
import math
import re

MODULE="CFS"
PACKAGE="calc_package_cfs.json"


def disabled(*args, **kwargs):
    raise RuntimeError("Analysis/design execution is disabled during BIM replay")


def disable_analysis():
    import cfs_engine, cfs_frame, cfs_pipeline, pipeline
    cfs_engine.run=disabled
    cfs_frame.run=disabled; cfs_frame.Frame2D.solve=disabled
    cfs_pipeline.design_and_report=disabled; pipeline.design_and_report=disabled
    try:
        import openseespy.opensees as ops
    except ImportError: return
    for name in ("analyze","eigen","responseSpectrumAnalysis"):
        if hasattr(ops,name): setattr(ops,name,disabled)


def frame_positions(cfg):
    if cfg.get("frame_positions_ft") is not None:
        positions=[float(v)*12 for v in cfg["frame_positions_ft"]]
        if len(positions)<2 or any(not math.isfinite(v) for v in positions) or any(b<=a for a,b in zip(positions,positions[1:])): raise ValueError("frame_positions_ft must be finite and increase")
        if cfg.get("n_frames") and int(cfg["n_frames"])!=len(positions): raise ValueError("Frame positions/count disagree")
        if cfg.get("length_ft") is not None and abs(positions[-1]-positions[0]-float(cfg["length_ft"])*12)>1e-6: raise ValueError("Frame positions/length disagree")
        return positions
    spacing=float(cfg["spacing_ft"])*12
    if spacing<=0: raise ValueError("Frame spacing must be positive")
    if cfg.get("n_frames") is not None:
        n=cfg["n_frames"]
        if int(n)!=n or n<2: raise ValueError("n_frames must be an integer >= 2")
        n=int(n)
    elif cfg.get("length_ft") is not None:
        bays=float(cfg["length_ft"])*12/spacing
        if abs(bays-round(bays))>1e-7: raise ValueError("Length is not an integer number of bays; declare frame_positions_ft")
        n=int(round(bays))+1
    else:
        raise ValueError("Declare n_frames, length_ft or frame_positions_ft; no representative-frame default")
    if cfg.get("length_ft") and abs((n-1)*spacing-float(cfg["length_ft"])*12)>1e-6: raise ValueError("Frame count, spacing and length disagree")
    return [i*spacing for i in range(n)]


def components(frame, indices):
    pending=set(indices)
    while pending:
        group={pending.pop()}; nodes={n for i in group for n in frame.elems[i][:2]}
        while True:
            attached={i for i in pending if nodes.intersection(frame.elems[i][:2])}
            if not attached: break
            group.update(attached); pending.difference_update(attached)
            nodes.update(n for i in attached for n in frame.elems[i][:2])
        degree={n:sum(n in frame.elems[i][:2] for i in group) for n in nodes}
        ends=sorted(n for n in nodes if degree[n]==1)
        # A member axis is straight. Preserve bends/branches as separate candidates.
        straight=len(ends)==2
        if straight:
            a,b=(frame.nodes[n] for n in ends)
            dx,dy=b[0]-a[0],b[1]-a[1]
            length=math.hypot(dx,dy)
            straight=length>1e-8 and all(abs(dx*(frame.nodes[n][1]-a[1])-dy*(frame.nodes[n][0]-a[0]))/length<1e-6 for n in nodes)
        if straight: yield sorted(group), ends
        else:
            for i in sorted(group): yield [i], list(frame.elems[i][:2])


def portal(cfg, pkg):
    import cfs_frame as CF
    from bim_export import issue
    frame, grouped, meta=CF.build_portal(cfg)
    try: positions=frame_positions(cfg)
    except (KeyError,ValueError,TypeError) as ex:
        return {"analysis":{"nodes":[],"elements":[]},"physical":{},"findings":[issue("frame_layout",str(ex))],"layout_evidence":{"frame_meta":meta}}
    nodes=[]; elements=[]; members=[]; findings=[]; profiles={}
    for label,indices in grouped.items():
        if not indices: continue
        name=cfg.get("col_section") if label.startswith("col") else cfg.get("raf_section")
        if label.startswith("kb"): name=(cfg.get("knee_braces") or {}).get("section")
        if name and name not in profiles:
            props=CF.frame_section(name)
            profiles[name]={"id":name,"status":"recovered","family":props["kind"],"properties":props,"dimensions":{},"basis":"Native CFS gross properties; built-up physical arrangement requires declaration"}
        for gi,(indices,ends) in enumerate(components(frame,indices),1):
            if len(ends)!=2:
                findings.append(issue("portal_member_chain","Branching frame chain needs physical member declaration",[label])); continue
            for fi,y in enumerate(positions,1):
                axis=[[float(frame.nodes[n][0]),y,float(frame.nodes[n][1])] for n in ends]
                mids=["cfs:frame:%d:element:%d"%(fi,i+1) for i in indices]
                members.append({"id":"cfs:frame:%d:member:%s:%d"%(fi,label,gi),"kind":"column" if label.startswith("col") else ("knee_brace" if label.startswith("kb") else "rafter"),
                                "status":"candidate","axis":axis,"local_y":[0.0,1.0,0.0],"profile_id":name,"material_id":None,
                                "reference_line":None,"end_offsets":None,"analysis_ids":mids,"geometry_basis":"analysis_axis"})
    for fi,y in enumerate(positions,1):
        for n,(x,z) in sorted(frame.nodes.items()): nodes.append({"id":"cfs:frame:%d:node:%s"%(fi,n),"point":[float(x),y,float(z)],"restraints":list(frame.fix.get(n,()))})
        for i,e in enumerate(frame.elems,1):
            elements.append({"id":"cfs:frame:%d:element:%d"%(fi,i),"node_ids":["cfs:frame:%d:node:%s"%(fi,n) for n in e[:2]],"label":e[4],"EA":e[2],"EI":e[3],"truss":e[5]})
    if cfg.get("structure_kind")=="component" or pkg.get("component_mode") or pkg.get("existing_structure_interface"):
        interfaces=[i for declaration in (cfg.get("bim",{}),pkg.get("bim",{})) for i in declaration.get("interfaces",[])]
        if not any(i.get("scope")=="existing" for i in interfaces):
            findings.append(issue("existing_shell_scope","Declare an existing-structure interface identifying the contextual frame and responsibility"))
        for member in members: member["scope"]="existing_context"
    return {"engine_version":"cfs_frame native builder", "analysis":{"nodes":nodes,"elements":elements,"comparison":{"status":"native_builder","source":"cfg.py; no schematic-viewer geometry"}},
            "physical":{"members":members,"profiles":list(profiles.values())},"findings":findings,
            "layout_evidence":{"frame_positions_in":positions,"frame_meta":meta,"secondary_note":"Purlins, girts, end walls and bracing require explicit physical members; design schedules are retained in design_records."}}


def parse_ranges(value):
    """Only accept an unambiguous numeric range list; never interpret descriptive prose."""
    if not isinstance(value,str): return None
    result=[]
    for part in value.split(","):
        match=re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*[-\u2013]\s*(-?\d+(?:\.\d+)?)\s*",part)
        if not match: return None
        a,b=map(float,match.groups())
        if b<=a: return None
        result.append((a,b))
    return result


def walls(cfg, pkg):
    from bim_export import issue
    heights=[float(v)*12 for v in cfg.get("heights_ft",[])]
    levels=[float(cfg.get("base_elevation_ft",0))*12]
    for h in heights: levels.append(levels[-1]+h)
    panels=[]; evidence=[]; unresolved=[]
    exact={(str(r.get("direction")),str(r.get("line"))):parse_ranges(r.get("braced_bays_ft")) for r in pkg.get("braced_bay_layout",[])}
    for direction,key in (("X","lines_x"),("Y","lines_y")):
        for line in cfg.get(key,[]):
            # WallLine.pos may be fitted for tributary-area analysis. A separate
            # plan position, when supplied, governs physical coordinates only.
            position_key = direction + "-" + str(line.name)
            plan_positions = cfg.get("line_plan_pos_ft", {})
            plan_pos = float(plan_positions.get(position_key, line.pos))
            if not math.isfinite(plan_pos):
                raise ValueError("Non-finite physical wall position: " + position_key)
            position_source = "cfg.py#/line_plan_pos_ft/" + position_key if position_key in plan_positions else "cfg.py WallLine.pos"
            # Optional explicit layout retained on WallLine, keyed by story.
            located=getattr(line,"segment_positions_ft",None) or {}
            for story,segs in sorted(line.segments.items()):
                story=int(story)
                if story<1 or story>=len(levels): raise ValueError("Wall story outside height declaration")
                ranges=located.get(story,located.get(str(story)))
                source="cfg.py WallLine.segment_positions_ft"
                if ranges is None:
                    ranges=exact.get((direction,str(line.name)))
                    source="calc_package_cfs.json#/braced_bay_layout"
                if ranges is not None:
                    ranges=[tuple(map(float,v)) for v in ranges]
                    if len(ranges)!=len(segs) or any(len(r)!=2 or r[1]<=r[0] or abs((r[1]-r[0])-float(s[0]))>1e-6 for r,s in zip(ranges,segs)):
                        ranges=None
                        source="range/length/count mismatch"
                entry={"id":"cfs:wall:%s:%s:story:%d"%(direction,line.name,story),"direction":direction,"line":str(line.name),
                       "position_in":plan_pos*12,"analysis_position_in":float(line.pos)*12,"position_source":position_source,"story":story,"base_z_in":levels[story-1],"segments_in":[[float(v)*12 for v in s] for s in segs],"range_source":source}
                evidence.append(entry)
                if not segs: continue
                if ranges is None:
                    unresolved.append(entry["id"]); continue
                for si,((a,b),(length,height)) in enumerate(zip(ranges,segs),1):
                    a*=12; b*=12; p=plan_pos*12; z=levels[story-1]; top=z+float(height)*12
                    points=[[a,p,z],[b,p,z],[b,p,top],[a,p,top]] if direction=="X" else [[p,a,z],[p,b,z],[p,b,top],[p,a,top]]
                    panels.append({"id":entry["id"]+":segment:"+str(si),"kind":"wall_segment","status":"candidate","vertices":points,
                                   "thickness":None,"material_id":None,"geometry_basis":source,"position_source":position_source})
    findings=[]
    if unresolved: findings.append(issue("wall_segment_positions","Supply actual along-line segment ranges; schematic distribution is not physical set-out",unresolved))
    return {"engine_version":"wall_line physical declarations; no spring solve", "analysis":{"nodes":[],"elements":[],"comparison":{"status":"not_applicable","source":"Wall spring model is not physical geometry"}},
            "physical":{"panels":panels},"layout_evidence":{"levels_in":levels,"wall_lines":evidence},"findings":findings}


def capture(cfg,pkg,root):
    return portal(cfg,pkg) if "span_ft" in cfg or cfg.get("spans") else walls(cfg,pkg)
