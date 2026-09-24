"""Reconstruct observed charging gaps and apparent idle SOC changes locally."""
from collections import Counter
from datetime import datetime
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
from garmin_fit_sdk import Decoder,Stream

from packfade.battery_history import classify_gap,mask_bracketed_50_glitches
from packfade.fit import has_battery_info,number

ROOT=Path(__file__).resolve().parent.parent


def extract():
    base=ROOT/"data/ebike_history"
    manifest=json.loads((base/"inventory.json").read_text())
    if manifest["ebike_only"] is not True or manifest["year_filter"] is not None:raise ValueError("Wrong cohort")
    rows=[]
    for key,info in sorted(manifest["rides"].items(),key=lambda kv:kv[1]["date"]):
        with (base/"raw"/f"{key}.fit").open("rb") as f:messages,errors=Decoder(Stream.from_buffered_reader(f)).read()
        if errors or not has_battery_info(messages):raise ValueError("Bad input - missing battery info")
        records=sorted([r for r in messages.get("record_mesgs",[]) if isinstance(r.get("timestamp"),datetime)],key=lambda r:r["timestamp"])
        date=records[0]["timestamp"]
        t=np.array([(r["timestamp"]-date).total_seconds() for r in records])
        def values(name):return np.array([number(r.get(name)) for r in records])
        raw=values("ebike_battery_level");raw[(raw<0)|(raw>100)]=np.nan
        suspect=mask_bracketed_50_glitches(t,raw)
        soc=raw.copy();soc[suspect]=np.nan
        indices=np.flatnonzero(np.isfinite(soc))
        row={"id":key,"record_start":date.isoformat(),"record_end":records[-1]["timestamp"].isoformat(),
             "glitch_samples_masked":int(suspect.sum()),"start_soc":None,"end_soc":None,
             "start_quality":"missing","end_quality":"missing"}
        if len(indices):
            first,last=indices[0],indices[-1];temp=values("temperature");dist=values("distance")
            for side,i,sel,offset in [("start",first,(t>=t[first])&(t<=t[first]+30),t[first]),
                                      ("end",last,(t>=t[last]-30)&(t<=t[last]),t[-1]-t[last])]:
                vals=soc[sel & np.isfinite(soc)]
                row[f"{side}_soc"]=float(soc[i])
                row[f"{side}_observed_at"]=records[i]["timestamp"].isoformat()
                row[f"{side}_offset_seconds"]=float(offset)
                row[f"{side}_quality"]="usable" if offset<=60 and np.ptp(vals)<=2 else "uncertain"
                tv=temp[sel & np.isfinite(temp)]
                row[f"{side}_temperature_c"]=float(np.median(tv)) if len(tv) else None
            row["residual_soc_rises_at_least_3pp"]=int((np.diff(soc[indices])>=3).sum())
            row["recorded_drop_pp"]=float(soc[first]-soc[last])
            row["km"]=float((dist[last]-dist[first])/1000) if np.isfinite(dist[[first,last]]).all() else None
            row["soc_span_hours"]=(t[last]-t[first])/3600
        rows.append(row)
    return rows


def intervals(rows):
    gaps=[]
    for a,b in zip(rows[:-1],rows[1:]):
        row={"previous_id":a["id"],"next_id":b["id"],"previous_ride":a["record_start"],"next_ride":b["record_start"],
             "classification":classify_gap(a,b)}
        if a["end_soc"] is not None and b["start_soc"] is not None:
            hours=(datetime.fromisoformat(b["start_observed_at"])-datetime.fromisoformat(a["end_observed_at"])).total_seconds()/3600
            row.update({"gap_hours":hours,"gap_days":hours/24,"before_soc":a["end_soc"],"after_soc":b["start_soc"],
                        "net_gain_pp":b["start_soc"]-a["end_soc"],"apparent_drop_pp":a["end_soc"]-b["start_soc"],
                        "previous_temperature_c":a["end_temperature_c"],"next_temperature_c":b["start_temperature_c"]})
            if a["end_temperature_c"] is not None and b["start_temperature_c"] is not None:
                row["temperature_change_c"]=b["start_temperature_c"]-a["end_temperature_c"]
            if hours<=0:row["classification"]="unknown_overlapping_recordings"
        gaps.append(row)
    return gaps


def describe(values):
    values=np.asarray(values,dtype=float)
    if not len(values):return None
    return {"n":len(values),"min":float(values.min()),"q25":float(np.percentile(values,25)),
            "median":float(np.median(values)),"q75":float(np.percentile(values,75)),"max":float(values.max()),"mean":float(values.mean())}


def cycles(rows,gaps):
    """Use only runs bounded by large likely charges; no missing-SOC bridging."""
    charges=[i for i,g in enumerate(gaps) if g["classification"]=="likely_charge_large_gain"]
    result=[]
    for left,right in zip(charges[:-1],charges[1:]):
        ride_rows=rows[left+1:right+1];inside=gaps[left+1:right]
        if not ride_rows or any(g["classification"] not in ["within_one_point","apparent_idle_drop"] for g in inside):continue
        if any(r.get("residual_soc_rises_at_least_3pp",1)>0 or r.get("km") is None or r["start_quality"]!="usable" or r["end_quality"]!="usable" for r in ride_rows):continue
        riding=sum(r["recorded_drop_pp"] for r in ride_rows)
        idle=sum(g["apparent_drop_pp"] for g in inside)
        total=ride_rows[0]["start_soc"]-ride_rows[-1]["end_soc"]
        if not np.isclose(riding+idle,total,atol=1e-5):continue
        result.append({"start":ride_rows[0]["record_start"],"end":ride_rows[-1]["record_end"],"rides":len(ride_rows),
                       "km":sum(r["km"] for r in ride_rows),"riding_drop_pp":riding,"between_ride_drop_pp":idle,"total_drop_pp":total})
    return result


def summarize(rows):
    """Charging profile built from per-recording endpoint rows (see extract())."""
    gaps=intervals(rows)
    charge=[g for g in gaps if g["classification"]=="likely_charge_large_gain"]
    topups=[g for g in gaps if g["classification"]=="possible_top_up"]
    idle=[g for g in gaps if g["classification"] in ["within_one_point","apparent_idle_drop"]]
    bands=[]
    for lo,hi,label in [(0,1,"Under 1 day"),(1,3,"1–3 days"),(3,7,"3–7 days"),(7,30,"7–30 days"),(30,np.inf,"Over 30 days")]:
        sub=[g for g in idle if lo<=g["gap_days"]<hi]
        bands.append({"label":label,"statistics":describe([g["apparent_drop_pp"] for g in sub])})
    session_cycles=cycles(rows,gaps)
    summary={"bike":"Raymon Trailray 160e / Simplo 720 Wh","all_rides_recorded_assumption":"Almost every ride recorded, as specified by user; missing SOC is still a barrier",
             "recordings":len(rows),"date_range":[rows[0]["record_start"][:10],rows[-1]["record_start"][:10]],
             "gap_counts":dict(Counter(g["classification"] for g in gaps)),
             "masked_50_samples":sum(r["glitch_samples_masked"] for r in rows),
             "rides_with_masked_50_samples":sum(r["glitch_samples_masked"]>0 for r in rows),
             "residual_rise_rides":sum(r.get("residual_soc_rises_at_least_3pp",0)>0 for r in rows),
             "likely_charges":{"count":len(charge),"pre_gap_soc":describe([g["before_soc"] for g in charge]),
                               "next_ride_soc":describe([g["after_soc"] for g in charge]),"gain_pp":describe([g["net_gain_pp"] for g in charge]),
                               "next_ride_at_least_95":sum(g["after_soc"]>=95 for g in charge),
                               "next_ride_at_least_99":sum(g["after_soc"]>=99 for g in charge),
                               "pre_gap_at_most_30":sum(g["before_soc"]<=30 for g in charge),
                               "pre_gap_over_50":sum(g["before_soc"]>50 for g in charge),
                               "gap_hours":describe([g["gap_hours"] for g in charge])},
             "possible_topups":topups,"idle":{"count":len(idle),"drop_pp":describe([g["apparent_drop_pp"] for g in idle]),
                                                   "exactly_unchanged":sum(g["apparent_drop_pp"]==0 for g in idle),
                                                   "within_one_point":sum(abs(g["apparent_drop_pp"])<=1 for g in idle),
                                                   "drops_at_least_5":sum(g["apparent_drop_pp"]>=5 for g in idle),
                                                   "sum_apparent_drop_pp":sum(g["apparent_drop_pp"] for g in idle),"duration_bands":bands,
                                                   "largest_drops":sorted(idle,key=lambda g:g["apparent_drop_pp"],reverse=True)[:8]},
             "cycles":{"count":len(session_cycles),"multi_ride_count":sum(c["rides"]>1 for c in session_cycles),"rows":session_cycles},
             "endpoints":rows,"gaps":gaps}
    # A duration association is descriptive only, not a self-discharge rate.
    if len(idle)>3:
        summary["idle"]["duration_drop_correlation"]=float(np.corrcoef([g["gap_days"] for g in idle],[g["apparent_drop_pp"] for g in idle])[0,1])
    return summary


def main():
    print("Reconstructing charging intervals and idle storage sag across rides...", flush=True)
    summary=summarize(extract())
    folder=ROOT/"reports/ebike_battery"
    folder.mkdir(parents=True,exist_ok=True)
    (folder/"charging_profile.json").write_text(json.dumps(summary,indent=2,allow_nan=False)+"\n")
    plot(summary,folder);report(summary,folder)
    print(json.dumps({k:v for k,v in summary.items() if k not in ["endpoints","gaps","cycles","possible_topups"]},indent=2))
    print('Clean charge-bounded runs:',summary['cycles']['count'],'; multi-ride:',summary['cycles']['multi_ride_count'])


def plot(s,folder):
    plt.rcParams.update({"font.size":10,"axes.spines.top":False,"axes.spines.right":False,
                         "figure.facecolor":"#fafbfe","axes.facecolor":"#fafbfe"})
    fig,axes=plt.subplots(3,1,figsize=(12,11),gridspec_kw={"height_ratios":[1.2,1.1,1]})
    fig.subplots_adjust(left=.1,right=.9,top=.87,bottom=.12,hspace=.48)
    cg=[g for g in s["gaps"] if g["classification"] in ["likely_charge_large_gain","possible_top_up"]]
    dates=[datetime.fromisoformat(g["next_ride"]) for g in cg]
    for d,g in zip(dates,cg):
        axes[0].plot([d,d],[g["before_soc"],g["after_soc"]],color="#adbacb",lw=1)
    axes[0].scatter(dates,[g["before_soc"] for g in cg],c="#d07d3b",s=28,label="Last recorded level before likely charge")
    axes[0].scatter(dates,[g["after_soc"] for g in cg],c="#278b79",s=28,label="First recorded level after gap")
    axes[0].set(ylabel="E-bike state of charge (%)",ylim=(0,105),title="Charging intervals · observation pairs, not a recorded charger curve")
    axes[0].xaxis.set_major_locator(mdates.MonthLocator(interval=3));axes[0].xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    axes[0].legend(fontsize=8,frameon=False,loc="lower right")
    idle=[g for g in s["gaps"] if g["classification"] in ["within_one_point","apparent_idle_drop"]]
    x=[g["gap_days"] for g in idle];y=[g["apparent_drop_pp"] for g in idle];c=[g.get("temperature_change_c",np.nan) for g in idle]
    sc=axes[1].scatter(x,y,c=c,cmap="coolwarm",vmin=-10,vmax=10,s=45,edgecolor="white",lw=.5)
    axes[1].axhspan(-1,1,color="#9caabc",alpha=.13);axes[1].axhline(0,color="grey",lw=.8)
    axes[1].set(xscale="log",xlabel="Time between last and next battery observations (days; log scale)",ylabel="Apparent drop (percentage points)",title="Gaps without a detected charge · a lower reading is not proof of self-discharge")
    fig.colorbar(sc,cax=fig.add_axes([.925,.40,.015,.18]),label="Next − previous recorded °C",extend="both")
    hist=[g["before_soc"] for g in s["gaps"] if g["classification"]=="likely_charge_large_gain"]
    if hist:
        axes[2].hist(hist,bins=np.arange(0,101,10),color="#d59a62",edgecolor="white",rwidth=.88)
        axes[2].axvline(np.median(hist),color="#7f492c",ls="--",label=f"Median last-seen level: {np.median(hist):.1f}%")
        axes[2].legend(frameon=False,fontsize=9)
    else:
        axes[2].text(0.5,0.5,"No likely charging intervals detected",ha="center",va="center",transform=axes[2].transAxes)
    axes[2].set(xlabel="Last observed battery level before a likely charge (%)",ylabel="Likely charging intervals",title="Best available plug-in-level proxy · exact plug-in SOC is unobserved",xticks=np.arange(0,101,10))
    for ax in axes:ax.grid(axis="y",alpha=.15)
    fig.suptitle("Your charging habits and apparent idle battery loss",x=.1,ha="left",fontsize=19,weight="bold")
    years_span = f"{s['date_range'][0][:4]}–{s['date_range'][1][:4]}" if s['date_range'][0][:4] != s['date_range'][1][:4] else s['date_range'][0][:4]
    fig.text(.1,.915,f"{s['recordings']} e-bike rides · {years_span} · {s['likely_charges']['count']} likely charging gaps (≥10 points) + {len(s['possible_topups'])} possible top-ups",fontsize=11)
    fig.text(.1,.033,"Assumption: almost all rides recorded. Exact charging time, duration and plug-in level were not logged.\n"
             "Small changes can reflect integer SOC reporting, temperature, gauge adjustment or unrecorded use.\n"
             "A ride without battery data breaks the history. Brief bracketed 50% glitches are flagged using offline look-ahead.",fontsize=9)
    fig.savefig(folder/"charging_profile.png",dpi=170);fig.savefig(folder/"charging_profile.svg");plt.close(fig)


def report(s,folder):
    ch=s["likely_charges"];idle=s["idle"]
    lines=["# Charging intervals and apparent idle battery loss","",f"{s['recordings']} e-bike recordings from {s['date_range'][0]} to {s['date_range'][1]}. Same Raymon / Simplo 720 Wh setup. Assume almost every ride was recorded, as requested. This is a separate analysis of all rides, including short rides excluded from the previous consumption model.","",
           "## Method and limits","","For each pair of adjacent recordings, compare the last valid e-bike SOC of the earlier recording with the first valid SOC of the next. Use their actual timestamps, not start-to-start time. Missing-SOC recordings break the chain. Endpoints must be within 60s of record boundaries and vary by no more than 2 points within their 30s neighborhood.","",
           "A gain ≥10 points is a likely charging interval; 5–9 is a possible top-up; 2–4 is ambiguous; ±1 is within reporting resolution; a fall ≥2 is an apparent idle drop. These are explicit analysis thresholds, not manufacturer specifications. No detected increase does not prove no charging occurred. Multiple charges or unrecorded usage inside a gap remain possible.","",
           f"Offline quality check: mask {s['masked_50_samples']} brief exactly-50% observations across {s['rides_with_masked_50_samples']} rides only when bracketed by near-equal readings at least 5 points away. Original FIT files remain unchanged. This is evidence-based anomaly handling, not a documented Yamaha sentinel rule. {s['residual_rise_rides']} ride(s) retain ≥3-point upward changes after this check.","",
           "The preceding end-of-ride SOC is a plug-in proxy only if charging followed without material SOC change. Neither the plug-in time nor the plug-in SOC is observed. The next ride's start is also not necessarily the charger stopping percentage. We cannot infer a current/voltage charge curve, duration on charge, or time stored above 80% from these endpoints.","",
           "## Charging pattern","",f"Classifications: {s['gap_counts']}",""]
    if ch["count"] > 0 and ch.get("pre_gap_soc"):
        lines.append(f"{ch['count']} likely charging intervals. Last observed pre-charge level: median {ch['pre_gap_soc']['median']:.1f}%, middle 50% {ch['pre_gap_soc']['q25']:.1f}–{ch['pre_gap_soc']['q75']:.1f}%. First reading on next ride: median {ch['next_ride_soc']['median']:.0f}%. {ch['next_ride_at_least_99']} start at ≥99%, {ch['next_ride_at_least_95']} at ≥95%. Median net SOC increase: {ch['gain_pp']['median']:.1f} points.\n")
    else:
        lines.append(f"{ch['count']} likely charging intervals detected in the available activity gaps.\n")

    lines += ["## Apparent idle changes",""]
    if idle["count"] > 0 and idle.get("drop_pp"):
        lines.append(f"{idle['count']} usable gaps have no detected charge. Median observed drop {idle['drop_pp']['median']:.1f} points; {idle['exactly_unchanged']} unchanged exactly; {idle['within_one_point']} within ±1 point; {idle['drops_at_least_5']} lose ≥5 points. Total net apparent loss {idle['sum_apparent_drop_pp']:.0f} points across these gaps is not a lifetime self-discharge measurement.\n")
    else:
        lines.append(f"{idle['count']} usable gaps have no detected charge.\n")
    lines += ["| Idle interval | Gaps | Median drop (points) | Maximum drop |","|---|---:|---:|---:|"]
    for b in idle["duration_bands"]:
        q=b["statistics"]
        if q:lines.append(f"| {b['label']} | {q['n']} | {q['median']:.1f} | {q['max']:.1f} |")
    lines += ["","Largest apparent drops:","","| Earlier ride | Next ride | Idle hours | SOC | Drop | Recorded temperature change |","|---|---|---:|---|---:|---:|"]
    for g in idle["largest_drops"]:
        lines.append(f"| {g['previous_ride'][:10]} | {g['next_ride'][:10]} | {g['gap_hours']:.1f} | {g['before_soc']:.0f} → {g['after_soc']:.0f}% | {g['apparent_drop_pp']:.0f} | {g.get('temperature_change_c',float('nan')):+.0f} °C |")
    lines += ["","Ambient/device endpoint temperatures are not storage or battery temperatures. Ride-end warmth and next-ride start conditions differ. Avoid fitting a chemical self-discharge correction from this table, especially by averaging per-gap percentage/day rates over very short gaps.","",
              "## Improvements to the model","",f"Built a charge-bounded accounting view: {s['cycles']['count']} clean observed runs between successive large likely charges, including {s['cycles']['multi_ride_count']} with multiple rides. Verify riding SOC drops + between-ride drops = run start SOC − run end SOC. This separates displayed charge lost while riding from changes while parked; it does not reveal energy capacity.","",
              "1. Keep original SOC and a quality mask. Use repeatable glitch rules with tests; do not throw away an entire good ride because of a one-second default-looking reading.",
              "2. Treat charging, riding, parking and gauge adjustments as separate states. Add an unknown state for missing data rather than silently joining across it.",
              "3. Compare charge-bounded runs and fixed SOC bands (for example 80–30%), with matched route/direction, assistance, speed and temperature. SOC-band choices are experiment settings, not charging advice.",
              "4. Distinguish reversible temperature/range effects from a slowly varying capacity term. Do not force all changes into a straight time-degradation coefficient. Unobserved storage temperature and time at high SOC need uncertainty, not invented values.",
              "5. Validate battery-use predictions on later whole runs. Compare with simple fixed-consumption baselines, and measure errors in SOC points. Bootstrap whole charge runs, not seconds.",
              "6. Add manual charge-event logging or a compatible charging-status feed: plug-in percentage, unplug percentage, timestamps and storage conditions. Actual measured battery discharge energy or manufacturer diagnostics is still needed to anchor true usable capacity.","",
              "Apparent-loss results from consumption regression remain provisional observational models, not lab-verified proofs of state of health. This charging analysis adds quality and accounting evidence; capacity models must be evaluated separately.","",
              "![Charging and idle profile](charging_profile.png)",""]
    (folder/"charging_profile.md").write_text("\n".join(lines))


if __name__=="__main__":main()
