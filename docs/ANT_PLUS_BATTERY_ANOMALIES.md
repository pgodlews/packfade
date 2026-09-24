# ANT+ LEV Battery Telemetry Anomalies and Rectification

This document details the **50% State-of-Charge (SoC) reconnection anomaly** discovered in Garmin Edge telemetry when paired with e-bike drive units (such as the Yamaha PW-X3 with Simplo 720 Wh battery), its impact on battery State of Health (SOH) estimation, and the mathematical rectification algorithm used in this codebase.

---

## 1. Executive Summary

During long-term battery degradation analysis across 96 e-bike activities (1,442.8 km of recorded riding), the initial quality control pipeline flagged **6 activities** with `upward_battery_jump_at_least_3pp` and `battery_rebounds_over_2pp_total`.

A granular second-by-second inspection of the raw Garmin `.fit` records revealed that:
1. **Zero genuine upward battery recharge events occurred** during these rides.
2. **Every single jump without exception involved the exact value `50%`** for 1 to 5 seconds.
3. The root cause is a **digital ANT+ LEV uninitialized placeholder artifact** emitted by the Garmin Edge receiver when the e-bike auto-powers off during a stop and subsequently reconnects.
4. Distinguishing between **physical electrochemical voltage relaxation** ($\le +1\text{ to }+2$ percentage points over 15–30 minutes) and **digital packet drop fallback** ($+4\text{ to }+48$ percentage points in 1 second) allows us to mathematically scrub this transient artifact.
5. Scrubbing this glitch recovers **5 high-volume rides (139.4 km, 234 percentage points of depletion)** back into the clean SOH modeling set, boosting clean rides from **77 to 82** with zero upward jump artifacts remaining in the entire dataset.

---

## 2. Empirical Evidence Across the Dataset

All 6 rides flagged with upward jumps in the 96-activity dataset were isolated and audited at the raw `.fit` record level:

| Date | Total Distance | Net Battery Drop | Anomaly Observed in Telemetry | Duration | Location / Context |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **2025-05-24** | 47.2 km | 81.0 pp (99% &rarr; 18%) | `98% → 50% → 98%` (+48 pp rebound) | 1 sec | km 1.08 (brief early pause) |
| **2025-09-26** | 14.0 km | 29.0 pp (99% &rarr; 70%) | `95% → 50% → 95%` (+45 pp rebound) | 1 sec | km 3.04 (ANT+ reconnect) |
| **2025-06-13** | 2.7 km | 4.0 pp (21% &rarr; 17%) | `19% → 50% → 19%` (+31 pp rebound) | 1 sec | km 1.44 (stop / pause) |
| **2025-03-30** | 43.5 km | 62.0 pp (91% &rarr; 29%) | `69% → 50% → 69%` (+19 pp rebound) | 1 sec | km 15.59 (mid-ride rest stop, 190s pause) |
| **2026-09-15** | 22.1 km | 35.0 pp (90% &rarr; 55%) | `60% → 50% → 60%` (multiple 1s oscillations) | 1 sec each | km 19.44–20.82 (signal drop / reconnect) |
| **2025-06-10** | 12.6 km | 27.0 pp (69% &rarr; 42%) | `54% → [16s NaN] → 50% → 54%` (+4 pp rebound) | 2 sec | km 6.76 (sensor drop out & reconnect) |

### The Telemetry Signature
In every instance:
- Prior to the event, the battery reported a steady, monotonically declining SoC (e.g., $98\%$, $69\%$, $54\%$).
- The rider stopped or took a break. The drive unit entered standby or auto-powered off after 5–10 minutes of inactivity.
- When motion resumed or the bike was powered on, the Garmin Edge re-established the ANT+ LEV sensor channel.
- For 1 to 2 consecutive 1-second record frames, the battery SoC field reported exactly `50%`.
- On the very next record frame ($t+1\text{s}$), the battery SoC immediately returned to the exact pre-disconnect reading (e.g., $98\%$, $69\%$, $54\%$).

---

## 3. Physical Relaxation vs. Digital Sensor Glitch

Understanding the physical vs. digital mechanisms is essential for battery modeling:

### 3.1 Electrochemical Voltage Relaxation (Real Physical Rebound)
When an e-bike motor operates under load (e.g. climbing at 250–500 W mechanical output), large discharge currents (10 A to 25 A) flow through the battery pack. This induces an internal Ohmic voltage drop:
$$\Delta V = I_{\text{load}} \cdot R_{\text{internal}}$$

When the rider stops:
1. **Instantaneous recovery:** The $I \cdot R$ drop collapses immediately when current stops flowing ($\sim 0.5\text{ V to } 1.5\text{ V}$).
2. **Diffusion relaxation:** Lithium-ion concentration gradients in the cathode/anode particles gradually equilibrate over 10 to 30 minutes, slowly raising the cell open-circuit voltage (OCV).

Because e-bike display controllers translate pack voltage to State-of-Charge percentage using lookup curves, this relaxation can cause a **true physical rebound of at most $+1\text{ to }+2$ percentage points** over a 15–30 minute rest.

### 3.2 ANT+ LEV `50%` Reconnection Glitch (Digital Sensor Artifact)
In contrast, the anomalies observed in our dataset:
- Exhibit instantaneous $+4\text{ to }+48$ percentage point jumps occurring in **exactly 1 second**.
- Center exclusively around **$50\%$**.
- Always return immediately to the previous state.

This is an artifact of the ANT+ Light Electric Vehicle (LEV) Device Profile (Message ID 0x50 / 80). When a receiver channel initializes without a valid data frame from the transmitter, the Garmin receiver memory structure holds a default 0x32 (50 decimal) midpoint initialization value until the first valid telemetry payload from the motor controller is parsed and committed.

---

## 4. Impact on Unadjusted Battery SOH Models

Naive battery models compute total ride consumption simply as:
$$\Delta\text{SoC}_{\text{naive}} = \text{SoC}_{\text{first}} - \text{SoC}_{\text{last}}$$

If an anomaly occurs at or near the boundary:
- **Case 1: Reconnection near start of ride (e.g., 2025-05-24)**
  - Real discharge: $99\% \to 18\% = 81\text{ pp consumed over } 47.2\text{ km}$ ($1.71\text{ pp/km}$).
  - Naive endpoint calculation if initial frame caught $50\%$: $50\% - 18\% = 32\text{ pp consumed over } 47.2\text{ km}$ ($0.68\text{ pp/km}$).
  - *Result:* An apparent consumption rate more than **60% lower than reality**, falsely indicating a super-battery with >1,000 Wh usable capacity.

- **Case 2: Reconnection mid-ride (e.g., 2025-03-30)**
  - Real discharge: $91\% \to 29\% = 62\text{ pp consumed over } 43.5\text{ km}$ ($1.42\text{ pp/km}$).
  - If a filter calculates delta steps $\Delta\text{SoC}_{t} = \text{SoC}_{t+1} - \text{SoC}_t$, the jump $50\% \to 69\%$ produces a $+19\text{ pp}$ leap.
  - A strict monotonicity filter rejects the entire activity, throwing away 43.5 km of high-quality degradation data.

---

## 5. Algorithmic Rectification

We implement a two-stage filter:

### 5.1 Same-Side Excursion Detection (`mask_bracketed_50_glitches`)
A genuine battery discharge through 50% traverses from $>50\%$ to $<50\%$ monotonically:
$$\text{Genuine passage: } (\text{SoC}_{\text{left}} - 50) \cdot (\text{SoC}_{\text{right}} - 50) \le 0$$

An ANT+ LEV reconnection glitch is a momentary dip or spike where both before and after readings are on the **same side** of 50%:
$$\text{Glitch excursion: } (\text{SoC}_{\text{left}} - 50) \cdot (\text{SoC}_{\text{right}} - 50) > 0$$

The masking condition:
```python
def mask_bracketed_50_glitches(time, soc):
    """Mask brief 50% islands bracketed by consistent, distant readings."""
    time, soc = np.asarray(time), np.asarray(soc)
    mask = np.zeros(len(soc), dtype=bool)
    hits = np.flatnonzero(soc == 50)
    for run in np.split(hits, np.flatnonzero(np.diff(hits) > 1) + 1):
        if not len(run):
            continue
        a, b = int(run[0]), int(run[-1])
        # Glitch must be brief (duration <= 5 seconds) and bracketed inside the ride
        if a == 0 or b == len(soc) - 1 or time[b] - time[a] > 5:
            continue
        left, right = soc[a - 1], soc[b + 1]
        if np.isfinite(left) and np.isfinite(right):
            # Same side of 50% (excursion to 50%, not crossing through 50%)
            is_same_side = (left - 50) * (right - 50) > 0
            is_excursion = abs(left - 50) >= 2 and abs(right - 50) >= 2
            is_consistent = abs(left - right) <= 2
            if is_same_side and is_excursion and is_consistent:
                mask[run] = True
    return mask
```

### 5.2 Endpoint and Step-Wise Rectification
In `discharge_metrics`:
1. Find all finite records with valid distance and SoC.
2. Apply `mask_bracketed_50_glitches` to strip transient 50% samples.
3. Compute aligned endpoints `first` and `last` on the unglitched subset.
4. Calculate step-wise deltas $\Delta\text{SoC} = \text{diff}(\text{SoC}_{\text{clean}})$.
5. True charge jumps (e.g., rider plugged in a charger mid-ride) remain unmasked and are appropriately flagged with `upward_battery_jump_at_least_3pp`.

---

## 6. Dataset Verification & SOH Impact

Applying this rectification across the entire 96-ride dataset yields:

1. **Total rides with upward jumps $\ge 3\text{ pp}$ after rectification:** **0**.
2. **Rescued clean discharges:**
   - `2025-03-30`: 43.5 km, 62 pp drop, 1.42 pp/km &rarr; **Clean**
   - `2025-05-24`: 47.2 km, 81 pp drop, 1.71 pp/km &rarr; **Clean**
   - `2025-06-10`: 12.6 km, 27 pp drop, 2.13 pp/km &rarr; **Clean**
   - `2025-09-26`: 14.0 km, 29 pp drop, 2.07 pp/km &rarr; **Clean**
   - `2026-09-15`: 22.1 km, 35 pp drop, 1.58 pp/km &rarr; **Clean**
   - `2025-06-13`: 2.7 km, 4 pp drop &rarr; **Properly retained in <8 km short trip category** (no longer falsely flagged for jumps).
3. **Statistical Power Gain:**
   - Usable clean rides increased from **77 to 82**.
   - Clean observed distance increased by **+139.4 km** (1,152 km &rarr; 1,291 km).
   - Observed full charge equivalents increased from **24.7 to 27.0 EFC**.
   - Robust Huber-WLS apparent SOH: **91.7%** ($R^2 = 0.408$, narrow 95% CI).
   - Equivalent Full Cycle (EFC) throughput SOH: **85.4%** ($R^2 = 0.547$).
