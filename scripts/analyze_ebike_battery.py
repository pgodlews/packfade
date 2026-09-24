"""Battery-use trends, with conditional capacity proxies and explicit limits.

State of charge is not measured usable capacity. This analysis cannot identify
true battery state of health without electrical energy or controlled demand.
"""
from collections import Counter, defaultdict
from datetime import datetime
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
from garmin_fit_sdk import Decoder, Stream

from packfade.battery_history import mask_bracketed_50_glitches
from packfade.fit import has_battery_info, number

ROOT = Path(__file__).resolve().parent.parent
NOMINAL_WH = 720


def weighted_mean(values, weights):
    ok = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    return float(np.average(values[ok], weights=weights[ok])) if ok.any() else None


def discharge_metrics(time, distance, soc):
    """Use aligned finite endpoints; scrub 50% ANT+ LEV reconnection glitches; reject true charge jumps."""
    valid = np.isfinite(time) & np.isfinite(distance) & np.isfinite(soc) & (soc >= 0) & (soc <= 100)
    idx = np.flatnonzero(valid)
    if len(idx) < 2:
        return {"reasons": ["missing_battery_or_distance"]}, None

    glitch_mask = mask_bracketed_50_glitches(time[idx], soc[idx])
    clean_idx = idx[~glitch_mask]
    if len(clean_idx) < 2:
        clean_idx = idx
    first, last = clean_idx[0], clean_idx[-1]
    delta = np.diff(soc[clean_idx])
    km = float((distance[last]-distance[first])/1000)
    drop = float(soc[first]-soc[last])
    reasons = []
    if km < 8: reasons.append("distance_under_8km")
    if drop < 15: reasons.append("battery_drop_under_15pp")
    if np.any(delta >= 3): reasons.append("upward_battery_jump_at_least_3pp")
    if np.any(np.diff(distance[clean_idx]) < -.1): reasons.append("distance_reset")
    if delta[delta > 0].sum() > 2: reasons.append("battery_rebounds_over_2pp_total")
    stats = {"km": km, "drop_pp": drop, "start_soc": float(soc[first]), "end_soc": float(soc[last]),
             "upward_jumps": int((delta >= 3).sum()), "largest_upward_jump_pp": float(max(0,delta.max())) if len(delta) else 0.0,
             "masked_50_glitches": int(glitch_mask.sum()),
             "pp_per_km": drop/km if km>0 else None,
             "equivalent_range_km": 100*km/drop if drop>0 else None,
             "reasons": reasons}
    return stats, (first, last)


def extract():
    archive=ROOT/"data/ebike_history"
    manifest=json.loads((archive/"inventory.json").read_text())
    if manifest["ebike_only"] is not True or manifest["year_filter"] is not None:
        raise ValueError("Requires e-bike-only history without year filter")
    rows=[]; record_fields=Counter(); battery_fields=Counter(); example=None
    for key, info in sorted(manifest["rides"].items(),key=lambda kv:kv[1]["date"]):
        with (archive/"raw"/f"{key}.fit").open("rb") as f:
            messages,errors=Decoder(Stream.from_buffered_reader(f)).read()
        if errors or not has_battery_info(messages): raise ValueError("Ineligible or corrupt battery activity file")
        records=sorted([r for r in messages.get("record_mesgs",[]) if isinstance(r.get("timestamp"),datetime)],key=lambda r:r["timestamp"])
        start=records[0]["timestamp"]
        t=np.array([(r["timestamp"]-start).total_seconds() for r in records])
        def values(name): return np.array([number(r.get(name)) for r in records])
        d,b,temp,mode=map(values,["distance","ebike_battery_level","temperature","ebike_assist_mode"])
        stats,ends=discharge_metrics(t,d,b)
        row={"date": start.isoformat(), **stats, "temperature_c": None,"speed_kmh": None,
             "mode7_fraction": None,"mode5_fraction": None,"mode3_fraction": None,
             "mode2_fraction": None,"ascent_m_per_km": None,"recorded_power_w": None}
        record_fields.update(set(str(k) for r in records for k,v in r.items() if v is not None))
        for mn, recs in messages.items():
            for r in recs:
                for k,v in r.items():
                    if v is not None and any(s in str(k).lower() for s in ["battery", "capacity", "voltage", "current", "energy"]):
                        battery_fields[f"{mn}.{k}"]+=1
        if ends:
            first,last=ends
            # Time weights cover each observed interval only up to 10s; no long
            # sensor holds. SOC endpoints and ride distance remain aligned.
            dt=np.r_[np.diff(t),0.]
            w=np.where((dt>0)&(dt<=10),dt,0.)
            span=(np.arange(len(t))>=first)&(np.arange(len(t))<last)
            w*=span
            battery_valid=np.isfinite(b)&(b>=0)&(b<=100)
            coverage=float(w[battery_valid].sum()/max(1,t[last]-t[first]))
            row["battery_time_coverage"]=coverage
            if coverage<.9: row["reasons"].append("battery_time_coverage_under_90pct")
            # Discard first 10 elapsed minutes for temperature equilibration.
            row["temperature_c"]=weighted_mean(temp,w*(t>=t[first]+600))
            if row["temperature_c"] is None: row["temperature_c"]=weighted_mean(temp,w)
            speed=values("enhanced_speed")
            if not np.isfinite(speed).any():speed=values("speed")
            row["speed_kmh"]=weighted_mean(speed*3.6,w*(speed>1))
            row["mean_soc"]=weighted_mean(b,w*battery_valid)
            mw=w*np.isfinite(mode)*(speed>1)
            for code in [7,5,3,2]:row[f"mode{code}_fraction"]=float(mw[mode==code].sum()/mw.sum()) if mw.sum()>0 else None
            row["recorded_power_w"]=weighted_mean(values("power"),w*(speed>1))
            sessions=messages.get("session_mesgs",[])
            if len(sessions)==1:
                ascent=number(sessions[0].get("total_ascent")); total=number(sessions[0].get("total_distance"))
                if np.isfinite(ascent) and total>0:row["ascent_m_per_km"]=float(ascent/(total/1000))
            if any(row[c] is None for c in ["temperature_c","speed_kmh","mean_soc","mode7_fraction"]):
                row["reasons"].append("missing_adjustment_covariate")
            if not row["reasons"]:
                row["nominal_assumption_wh_per_km"]=NOMINAL_WH*row["pp_per_km"]/100
                if example is None:example={"time":t,"soc":b,"temperature":temp,"date":start.date().isoformat()}
        rows.append(row)
    cum_drop = 0.0
    for r in rows:
        # Quantization and environmental variance via Delta Method:
        # 1. 1% integer quantization on start/end SoC gives Var(Delta_SoC) = 1/12 + 1/12 = 1/6.
        #    By Delta Method on ln(Delta_SoC), Var_quant = 1 / (6 * Delta_SoC^2).
        # 2. Environmental process noise floor: sigma_env = 0.05 (5% relative log-scale noise
        drop_val = max(1.0, float(r.get("drop_pp") or 1.0))
        r["quantization_weight"] = float(1.0 / (0.05**2 + 1.0 / (6.0 * (drop_val**2))))
        if not r["reasons"]:
            cum_drop += r["drop_pp"]
            r["cumulative_drop_pp"] = float(cum_drop)
            r["cumulative_efc"] = float(cum_drop / 100.0)
        else:
            r["cumulative_drop_pp"] = None
            r["cumulative_efc"] = None
    return rows,dict(record_fields),dict(battery_fields),example


def fit_trend(rows, columns, origin, end, bootstrap=2000, time_col="years", use_wls=False, use_huber=False, filter_clean=True):
    rows=[r for r in rows if (not r["reasons"] if filter_clean else True) and all(r.get(c) is not None for c in columns)]
    if len(rows)<max(15,3*(len(columns)+2)):return None
    dates=[datetime.fromisoformat(r["date"]) for r in rows]
    orig=origin.replace(tzinfo=dates[0].tzinfo) if dates[0].tzinfo else origin
    if time_col=="efc":
        t_raw=np.array([r.get("cumulative_efc",0.0) for r in rows])
    else:
        t_raw=np.array([(d-orig).total_seconds()/(365.25*86400) for d in dates])
    y=np.log([r["pp_per_km"] for r in rows])
    raw=np.column_stack([t_raw]+[np.array([r[c] for r in rows]) for c in columns])
    scale=raw.std(axis=0); scale[scale==0]=1
    x=np.column_stack([np.ones(len(rows)),(raw-raw.mean(axis=0))/scale])
    if np.linalg.matrix_rank(x)<x.shape[1]:return None

    if use_wls:
        # Inverse-variance weights combining Delta-method quantization noise and 5% environmental floor
        w_base=np.array([r.get("quantization_weight") or (1.0 / (0.05**2 + 1.0 / (6.0 * max(1.0, float(r.get("drop_pp", 1.0)))**2))) for r in rows],dtype=float)
        w_base/=max(1e-6,w_base.mean())
    else:
        w_base=np.ones(len(rows),dtype=float)

    def solve_model(x_mat, y_vec, weights):
        if use_huber:
            cur_w=weights.copy()
            W=np.diag(cur_w)
            c=np.linalg.solve(x_mat.T @ W @ x_mat, x_mat.T @ (cur_w * y_vec))
            for _ in range(10):
                res=y_vec - x_mat @ c
                mad=np.median(np.abs(res - np.median(res)))
                s=1.4826*max(mad, 1e-4)
                u=np.abs(res)/s
                huber=np.where(u<=1.345, 1.0, 1.345/np.maximum(u, 1e-4))
                cur_w=weights*huber
                W=np.diag(cur_w)
                new_c=np.linalg.solve(x_mat.T @ W @ x_mat, x_mat.T @ (cur_w * y_vec))
                if np.max(np.abs(new_c - c)) < 1e-5:
                    c = new_c
                    break
                c = new_c
            return c, cur_w
        elif use_wls:
            W=np.diag(weights)
            c=np.linalg.solve(x_mat.T @ W @ x_mat, x_mat.T @ (weights * y_vec))
            return c, weights
        else:
            c=np.linalg.lstsq(x_mat, y_vec, rcond=None)[0]
            return c, weights

    coef, final_w=solve_model(x, y, w_base)
    beta=float(coef[1]/scale[0])
    index_end=min(end.replace(tzinfo=dates[0].tzinfo) if dates[0].tzinfo else end, max(dates))
    if time_col=="efc":
        duration=float(rows[-1].get("cumulative_efc",0.0))
    else:
        duration=(index_end-orig).total_seconds()/(365.25*86400)

    groups=defaultdict(list)
    for i,d in enumerate(dates):groups[d.date().isoformat()].append(i)
    groups=list(groups.values());rng=np.random.default_rng(7);betas=[]
    for _ in range(bootstrap):
        indices=np.concatenate([groups[i] for i in rng.integers(0,len(groups),len(groups))])
        if np.linalg.matrix_rank(x[indices])<x.shape[1]:continue
        try:
            c,_=solve_model(x[indices], y[indices], w_base[indices])
            betas.append(c[1]/scale[0])
        except Exception:
            continue
    if len(betas)>=50:
        beta_ci=np.percentile(betas,[2.5,97.5])
        ci=100*np.exp(-beta_ci[::-1]*duration)
        slope_bootstrap_95=beta_ci.tolist()
        apparent_index_95=ci.tolist()
    else:
        slope_bootstrap_95=None
        apparent_index_95=None
    index=100*np.exp(-beta*duration)
    prediction=x@coef
    temp_idx=columns.index("temperature_c") if "temperature_c" in columns else -1
    beta_temp=float(coef[temp_idx+2]/scale[temp_idx+1]) if temp_idx>=0 else None
    temp_sens=float(np.exp(beta_temp*10)-1.0) if beta_temp is not None else None

    if use_wls or use_huber:
        w_mean=np.average(y, weights=final_w)
        r_sq=float(1.0 - np.sum(final_w*(y-prediction)**2)/np.sum(final_w*(y-w_mean)**2))
    else:
        r_sq=float(1.0 - np.sum((y-prediction)**2)/np.sum((y-y.mean())**2))

    return {"rides":len(rows),"dates":len(groups),"columns":columns,"index_date":index_end.date().isoformat(),
            "time_variable":time_col,"use_wls":use_wls,"use_huber":use_huber,
            "annual_log_consumption_slope":beta,"log_consumption_slope":beta,
            "slope_bootstrap_95":slope_bootstrap_95,"apparent_capacity_index_end":float(index),
            "apparent_index_95":apparent_index_95,"apparent_loss_percent":float(100-index),
            "conditional_capacity_wh":float(NOMINAL_WH*index/100),
            "in_sample_r_squared":r_sq,
            "temperature_log_effect_per_c":beta_temp,
            "temperature_sensitivity_per_10c":temp_sens,
            "date_range":[min(dates).date().isoformat(),max(dates).date().isoformat()],
            "condition_number":float(np.linalg.cond(x)),"successful_bootstraps":len(betas)}


def build_models(valid,origin,end):
    """All trend models reported in analysis.json, fitted on clean rides."""
    core=["temperature_c","speed_kmh","mean_soc","mode5_fraction","mode3_fraction","mode2_fraction"]
    specs={"Unadjusted":[],"Temperature only":["temperature_c"],"Temperature + speed + SOC + assistance":core}
    models={name:fit_trend(valid,cols,origin,end) for name,cols in specs.items()}
    # Enhanced robust models:
    models["Robust WLS (Quantization + Huber + Controls)"]=fit_trend(valid,core,origin,end,use_wls=True,use_huber=True)
    models["Cycle Throughput (EFC + Controls)"]=fit_trend(valid,core,origin,end,time_col="efc",use_wls=True,use_huber=True)
    climbs=[r for r in valid if r["ascent_m_per_km"] is not None]
    models["Complete elevation subset, core controls"]=fit_trend(climbs,core,origin,end)
    models["Complete elevation subset, plus climbing"]=fit_trend(climbs,core+["ascent_m_per_km"],origin,end)
    stable=[r for r in valid if r["mode7_fraction"]>=.95 and 12<=r["km"]<=18 and 12<=r["temperature_c"]<=24]
    models["Similar distance/temp, >=95% mode 7"]=fit_trend(stable,["temperature_c","speed_kmh","mean_soc"],origin,end)
    power=[r for r in climbs if r["recorded_power_w"] is not None]
    models["Elevation/power subset, plus recorded cycle power"]=fit_trend(power,core+["ascent_m_per_km","recorded_power_w"],origin,end)
    return models


def quarterly_summary(valid):
    quarterly=[]
    groups=defaultdict(list)
    for r in valid:
        d=datetime.fromisoformat(r["date"]);groups[f"{d.year} Q{(d.month-1)//3+1}"].append(r)
    for label,rs in groups.items():
        quarterly.append({"period":label,"rides":len(rs),"median_pp_per_km":float(np.median([r["pp_per_km"] for r in rs])),
                          "median_equivalent_range_km":float(np.median([r["equivalent_range_km"] for r in rs])),
                          "median_temperature_c":float(np.median([r["temperature_c"] for r in rs])),
                          "median_mode7_fraction":float(np.median([r["mode7_fraction"] for r in rs]))})
    return quarterly


def main():
    print("Extracting battery timeseries and filtering clean discharges...", flush=True)
    rows,fields,battery_fields,example=extract()
    valid=[r for r in rows if not r["reasons"]]
    if len(valid)<15:raise ValueError("Too few clean rides to inspect a trend")
    print(f"Extracted {len(rows)} battery rides ({len(valid)} clean discharges). Fitting degradation models...", flush=True)
    origin=datetime.fromisoformat(rows[0]["date"]);end=datetime.fromisoformat(valid[-1]["date"])
    models=build_models(valid,origin,end)
    reasons=Counter(reason for r in rows for reason in r["reasons"])
    quarterly=quarterly_summary(valid)
    summary={"bike":"Raymon Trailray 160e","motor":"Yamaha PW-X3","battery":"Simplo 720 Wh",
             "same_bike_battery_assumed":True,"baseline_capacity_assumed_percent":100,"baseline_date":origin.date().isoformat(),
             "latest_date":end.date().isoformat(),"latest_recording_date":rows[-1]["date"][:10],"rides_imported":len(rows),"rides_usable":len(valid),
             "exclusion_reason_counts_overlapping":dict(reasons),"rides_with_record_field":fields,
             "battery_energy_field_audit":battery_fields,"quarterly":quarterly,"models":models,
             "observed_total_km":float(sum(r.get("km",0) for r in rows)),
             "clean_observed_soc_depletion_full_charge_equivalents":sum(r["drop_pp"] for r in valid)/100,
             "temperature_range_c":[min(r["temperature_c"] for r in valid),max(r["temperature_c"] for r in valid)],
             "true_state_of_health_identifiable":False,"per_ride":rows}
    folder=ROOT/"reports/ebike_battery";folder.mkdir(parents=True,exist_ok=True)
    (folder/"analysis.json").write_text(json.dumps(summary,indent=2,allow_nan=False)+"\n")
    plot(summary,folder)
    report(summary,folder)
    print(json.dumps({k:v for k,v in summary.items() if k not in ["per_ride","battery_energy_field_audit","rides_with_record_field"]},indent=2))


def plot(s,folder):
    rows=[r for r in s["per_ride"] if not r["reasons"]]
    plt.rcParams.update({"font.size":10,"axes.spines.top":False,"axes.spines.right":False,
                         "figure.facecolor":"#fafbfe","axes.facecolor":"#fafbfe"})
    fig,axes=plt.subplots(3,1,figsize=(12,11),gridspec_kw={"height_ratios":[1.4,1,1.35]})
    fig.subplots_adjust(left=.095,right=.86,top=.9,bottom=.12,hspace=.46)
    dates=[datetime.fromisoformat(r["date"]) for r in rows]
    rate=np.array([r["pp_per_km"] for r in rows]);temp=np.array([r["temperature_c"] for r in rows])
    scatter=axes[0].scatter(dates,rate,c=temp,cmap="coolwarm",vmin=8,vmax=28,s=38,edgecolor="white",linewidth=.4)
    groups=defaultdict(list)
    for i,d in enumerate(dates):groups[(d.year,d.month)].append(i)
    for (year,month),indices in groups.items():
        if len(indices)>=3:
            x=datetime(year,month,15,tzinfo=dates[0].tzinfo)
            q=np.percentile(rate[indices],[25,50,75])
            axes[0].errorbar(x,q[1],yerr=[[q[1]-q[0]],[q[2]-q[1]]],fmt="_",color="#162c48",markersize=17,lw=2)
    axes[0].set(ylabel="Battery percentage points / km",title="Observed battery use · lower means less charge used per kilometre")
    axes[0].xaxis.set_major_locator(mdates.MonthLocator(interval=3));axes[0].xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    fig.colorbar(scatter,cax=fig.add_axes([.89,.70,.017,.17]),label="Recorded temperature (°C)")
    mode=np.array([r["mode7_fraction"] for r in rows])
    axes[1].scatter(dates,mode*100,c="#287e79",s=25)
    axes[1].set(ylabel="Time in assistance code 7 (%)",ylim=(-5,105),title="Assistance choice changes too · code numbers are sensor-specific")
    axes[1].xaxis.set_major_locator(mdates.MonthLocator(interval=3));axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    model=s["models"]["Temperature + speed + SOC + assistance"]
    origin=datetime.fromisoformat(s["baseline_date"]);end=datetime.fromisoformat(s["latest_date"])
    days=np.arange((end-origin).days+1)
    from datetime import timedelta
    dd=[origin+timedelta(days=int(d)) for d in days]
    for name,color in [("Unadjusted","#9ca8b8"),("Temperature only","#bd9248"),("Temperature + speed + SOC + assistance","#2563b7")]:
        m=s["models"][name]
        if not m:continue
        yy=100*np.exp(-m["annual_log_consumption_slope"]*days/365.25)
        axes[2].plot(dd,yy,color=color,lw=2,label=name)
    if model and model.get("slope_bootstrap_95"):
        lo,hi=model["slope_bootstrap_95"]
        axes[2].fill_between(dd,100*np.exp(-hi*days/365.25),100*np.exp(-lo*days/365.25),color="#2563b7",alpha=.13)
    axes[2].axhline(100,color="grey",lw=.8,ls="--")
    axes[2].set(ylabel="Conditional capacity proxy (%)",title="Assume equal electrical demand after adjustment · oldest date anchored at 100%")
    axes[2].xaxis.set_major_locator(mdates.MonthLocator(interval=3));axes[2].xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    axes[2].legend(fontsize=8,frameon=False,loc="best")
    for ax in axes:ax.grid(alpha=.12)
    fig.suptitle("Can your e-bike rides reveal battery degradation?",x=.095,ha="left",fontsize=19,weight="bold")
    years_span = f"{s['baseline_date'][:4]}–{s['latest_date'][:4]}" if s['baseline_date'][:4] != s['latest_date'][:4] else s['baseline_date'][:4]
    fig.text(.095,.927,f"{s.get('bike', 'e-Bike')} · {s.get('battery', 'Battery')} · {s['rides_imported']} rides inspected / {s['rides_usable']} usable · {years_span}",fontsize=11)
    fig.text(.095,.035,"Bottom: fitted observational trends, NOT measured battery health. Shading: 95% date-bootstrap interval for adjusted trend.\n"
             "Unknown wind, terrain, rider effort and battery temperature remain. Above 100% does not mean the battery regenerated.\n"
             "Top: monthly median/IQR shown when ≥3 rides. Charge jumps, short rides and poor coverage excluded; full details in report.",fontsize=9)
    fig.savefig(folder/"battery_history.png",dpi=170);fig.savefig(folder/"battery_history.svg");plt.close(fig)
    models=[(name,m) for name,m in s["models"].items() if m]
    fig,ax=plt.subplots(figsize=(11,5.5));fig.subplots_adjust(left=.39,right=.96,top=.82,bottom=.22)
    for i,(name,m) in enumerate(models):
        value=m["apparent_capacity_index_end"]
        if m.get("apparent_index_95"):
            lo,hi=m["apparent_index_95"]
            ax.errorbar(value,i,xerr=[[value-lo],[hi-value]],fmt="o",color="#2563b7",capsize=4)
        else:
            ax.plot(value, i, "o", color="#2563b7")
    ax.axvline(100,color="grey",ls="--");ax.set(yticks=range(len(models)),yticklabels=[f"{n}\n(n={m['rides']}; through {m['index_date']})" for n,m in models],xlabel="Conditional capacity proxy at each subset's final ride (%)")
    ax.invert_yaxis();ax.grid(axis="x",alpha=.2)
    fig.suptitle("Model estimates depend on controls and available history",fontsize=15,weight="bold")
    fig.text(.05,.065,"Bars: 95% date-bootstrap intervals; uncertainty excludes unmeasured confounding.\n"
             "Subsets end on different dates: these are not like-for-like current health estimates. No future extrapolation.\n"
             "None measures electrical capacity. Original-date capacity is assumed 100% for all curves.",fontsize=9)
    fig.savefig(folder/"model_sensitivity.png",dpi=170);fig.savefig(folder/"model_sensitivity.svg");plt.close(fig)


def report(s,folder):
    lines=["# E-bike battery investigation", "",f"Bike: {s['bike']}; motor: {s['motor']}; battery: {s['battery']}. User confirms the same bike and supplies the assumption of 100% original capacity at the oldest ride, {s['baseline_date']}.","",
           f"Available device history: {s['rides_imported']} e-bike rides through {s['latest_recording_date']}; {s['rides_usable']} pass the analysis filters, most recently {s['latest_date']}. This covers files currently available on the Edge, not a guarantee of all lifetime use. Previous non-e-bike / 2026-only study remains separate.","",
           "## What can be estimated", "", "We can estimate battery-use changes and a conditional relative-capacity proxy. We cannot identify true state of health from these FIT files. Electrical Wh/Ah delivered by the traction battery and cell temperature are not identified. The battery_mesgs.capacity field is percentage, not full-charge Wh, and its values differ from e-bike SOC; it appears to describe the recording device. Accessory battery voltages are not traction-battery energy measurements. Predicted remaining range is not an independent capacity measurement.","",
           "A scalar 250 W motor rating is not measured electrical draw. Recorded cycling power is not assumed to be battery power; it enters only a labelled sensitivity analysis. Assistance codes are treated as categories/fractions, never numerical multipliers or assumed named modes.","",
           "## Filtering and measurements", "", "Only e-bike activities, all available years. Use finite aligned SOC/distance endpoints; require ≥8 km, ≥15 percentage points consumed, ≥90% observed battery-time coverage, no ≥3-point upward jump, no >2 points of total rebounds and no distance reset. Reject ambiguous charge/swap/reconnect episodes instead of interpreting them as degradation. Temperature is time-weighted after the first 10 minutes when possible, to reduce initial device-temperature transients; it is not battery-cell temperature.","",
           f"Exclusions (reasons overlap): {s['exclusion_reason_counts_overlapping']}","",
           "Consumption is (start SOC − end SOC) / kilometres. Full-charge equivalent range is 100 / consumption; it extrapolates a partial discharge and is not a measured full-to-empty ride. Multiplying consumption by 7.2 Wh per percentage point assumes 720 Wh is still usable, so that conversion cannot independently measure degradation.","",
           f"Usable rides contain {s['clean_observed_soc_depletion_full_charge_equivalents']:.1f} full-charge equivalents of observed SOC depletion. This is not the battery's lifetime cycle count; charging and unrecorded use are absent.","",
           "## Raw period comparisons", "", "| Period | Rides | Median percentage points/km | Equivalent range km | Median recorded °C | Median mode-7 share |", "|---|---:|---:|---:|---:|---:|"]
    for q in s["quarterly"]:lines.append(f"| {q['period']} | {q['rides']} | {q['median_pp_per_km']:.2f} | {q['median_equivalent_range_km']:.1f} | {q['median_temperature_c']:.1f} | {q['median_mode7_fraction']*100:.0f}% |")
    latest_rec = s.get("latest_recording_date", s["latest_date"])
    lines += ["", "## Conditional trend models", "", "Fit log(consumption per km) = intercept + time + selected controls using ordinary least squares and equal weight per eligible ride. Time is continuous years; controls use recorded temperature, moving speed, mean SOC and mode 5/3/2 fractions (other modes absorbed by the reference category). Additional models restrict distance/temp/assistance or include ascent and recorded cycle power where available. Exact GPS route, wind, tyre/terrain changes and battery temperature remain uncontrolled. This is descriptive regression, not a validated future predictor.","",
              "Assuming constant electrical demand after those controls, relative capacity at time t is 100 × exp(−time coefficient × t). Anchor this fitted curve to 100% at the oldest date, as requested. The first actual ride is too short for the trend filters and is not a precision capacity test. Confidence bands resample whole dates, keeping same-day rides together; they do not cover bias from missing variables or the truth of the baseline assumption. Values above 100% expose demand/model uncertainty; do not interpret them as regained battery capacity.","",
              f"The latest recorded ride ({latest_rec}) may be excluded if it has upward SOC jumps or fails quality filters. Several recordings lack optional channels like elevation or cycling power. Models requiring these channels end earlier and cannot estimate current health; their indices below stop at their own final observed date. A straight time trend cannot fully describe non-linear or seasonal variations in consumption.","",
              "| Model | Rides | Last observation | Apparent index then | 95% bootstrap interval | Apparent loss | Conditional Wh |", "|---|---:|---|---:|---:|---:|---:|"]
    for name,m in s["models"].items():
        if not m:continue
        ci_str = f"{m['apparent_index_95'][0]:.1f}–{m['apparent_index_95'][1]:.1f}%" if m.get("apparent_index_95") else "—"
        lines.append(f"| {name} | {m['rides']} | {m['index_date']} | {m['apparent_capacity_index_end']:.1f}% | {ci_str} | {m['apparent_loss_percent']:+.1f}% | {m['conditional_capacity_wh']:.0f} |")
    lines += ["", "![History](battery_history.png)","", "![Sensitivity](model_sensitivity.png)","",
              "## Product direction", "", "A Garmin field could track standardized battery-use trends and flag unusual consumption. It should not label SOC-derived trend alone as measured battery health. For usable capacity, obtain manufacturer-compatible battery diagnostics or measured DC discharge Wh/Ah under a controlled procedure; compare repeatable rides at similar pack temperature, assistance and tyre setup. A plug-in wall meter measures charger input energy, including losses, not direct usable battery capacity. Do not open the battery or improvise discharge wiring.","",
              "Temperature normalization here is an observed association, not a manufacturer battery chemistry correction. Different road demand can mask or mimic degradation even after adjustment.","",
              "## Sources", "", "- [Yamaha battery FAQ](https://global.yamaha-motor.com/business/e-bike-systems/faq/): range is affected by tyre/chain condition and cold, as well as battery wear.",
              "- [Garmin temperature readings](https://www8.garmin.com/manuals/webhelp/edge520plus/EN-GB/GUID-0FBC73F5-6726-4886-95CD-4BD4E152178F.html): device temperature can differ from air temperature.",
              "- [Official FIT SDK](https://github.com/garmin/fit-python-sdk): local profile defines e-bike battery level and battery capacity fields in percent; optional fields do not establish source identity by their name alone.","",
              "Private FIT files, dates and analysis remain in ignored data/ebike_history and reports/ebike_battery folders. No GPS tracks were uploaded for this investigation."]
    (folder/"analysis.md").write_text("\n".join(lines)+"\n")


if __name__=="__main__":main()
