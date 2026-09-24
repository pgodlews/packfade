# Mathematical and Statistical Foundations of E-Bike Battery Degradation

This document details the mathematical, statistical, and signal-processing formulations used in Packfade to extract an **Apparent Consumption-Normalized Capacity Index** (an observational pack health proxy) from consumer e-bike telemetry.

The canonical production implementation of these algorithms runs client-side in the web browser (`web/index.html`) using dependency-free JavaScript linear algebra (Cholesky factorization, Huber Iteratively Reweighted Least Squares, and clustered bootstrap resampling). The Python codebase in `scripts/` served as the exploratory proof-of-concept (POC) tool and offline replication benchmark.

---

## 1. Problem Formulation & Observational Nature

### 1.1 The Observable vs. True Usable Capacity
In consumer e-bike systems (e.g. Yamaha PW-X3, Bosch Smart System, Shimano EP8, Specialized Turbo), head units record the State of Charge ($\text{SoC} \in [0, 100]\%$) transmitted over the ANT+ LEV (Light Electric Vehicle) profile.

Head units do **not** record:
- Total accumulated discharge ampere-hours ($\text{Ah}$) or pack energy throughput ($\text{Wh}$).
- Individual cell series voltages or pack internal resistance ($R_{\text{int}}$).
- Core battery cell temperature ($T_{\text{cell}}$). Only ambient external air temperature ($T_{\text{ambient}}$) is captured by the Garmin barometer/thermistor.
- High-rate shunt current measurements ($I(t)$).

Consequently, battery State of Health cannot be directly identified from observational telemetry in the manner of a laboratory-controlled constant-current discharge test (e.g., a Bosch DiagnosticTool or a Yamaha CC/CV discharge load bank). Instead, Packfade formulates battery degradation as a **conditional consumption rate regression** across hundreds of kilometers of real-world riding.

### 1.2 The Core Observable
For each eligible ride $i$, we define the empirical battery consumption rate $y_i$:
$$y_i = \frac{\Delta\text{SoC}_i}{d_i} \quad \left[\frac{\text{percentage points}}{\text{km}}\right]$$

where:
- $\Delta\text{SoC}_i = \text{SoC}_{\text{start}, i} - \text{SoC}_{\text{end}, i}$ is the net percentage drop.
- $d_i$ is the distance traversed in kilometers.

As an electrochemical cell degrades, its internal resistance increases and usable capacity decreases. Under strictly identical external physical demands (same rider power, elevation gain, assistance mode, ambient temperature, rolling resistance, and aerodynamics), an aged pack must surrender a larger fraction of its remaining capacity to traverse the same distance:
$$\text{Capacity} \downarrow \implies y_i = \frac{\Delta\text{SoC}_i}{d_i} \uparrow$$

---

## 2. Threats to Identification & Physical Limitations

Because Packfade operates on real-world observational data rather than controlled bench experiments, inferring capacity loss from consumption rates is subject to econometric and physical identification challenges.

### 2.1 Econometric Identification vs. Statistical Robustness
The statistical machinery in Packfade—Quantization-Weighted Least Squares (WLS), Huber M-estimation, and date-clustered block bootstrapping—solves three specific statistical problems:
1. **Heteroskedasticity from integer discretization:** Short rides with small $\Delta\text{SoC}$ have high relative quantization noise; WLS weights rides inversely to their error variance.
2. **Gross outlier contamination:** Severe headwinds, muddy detours, or heavy backpack loads produce heavy-tailed residuals; Huber IRLS bounds their influence.
3. **Pseudo-replication:** Multi-leg rides on the same day share temperature, tire pressure, and battery aging state; cluster bootstrap generates honest confidence intervals.

However, **statistical robustness cannot cure omitted variable bias or solve the structural identification problem**. If unobserved energy demands trend systematically over time, that trend leaks directly into the estimated degradation slope $\beta_t$.

### 2.2 Omitted Physical and Operational Confounders
In field telemetry from mountain bikes (such as the Raymon Trailray 160e / Yamaha PW-X3), several physical variables remain unmeasured by Garmin ANT+ LEV streams:

1. **Trail Roughness & Surface Rolling Resistance ($C_{rr}$):**
   A change in surface from hardpack gravel or asphalt ($C_{rr} \approx 0.006$) to soft loam or sticky mud ($C_{rr} \ge 0.020\text{--}0.030$) increases rolling resistance by $200\text{--}400\%$. If a rider transitions toward rougher trails or wet seasons over time, the apparent consumption rate increases even if pack capacity is unchanged.

2. **Tire Pressure and Tread Compound:**
   High-volume trail tires (e.g. 29 $\times$ 2.4″ at 1.4 bar vs 1.8 bar) exhibit pronounced carcass viscoelastic hysteresis losses. A seasonal drop in tire pressure directly increases mechanical energy dissipation per kilometer.

3. **Stop-Start Dynamics & Non-Linear Motor Torque Curves:**
   The Yamaha PW-X3 motor does not deliver assist power as a function of speed alone; it uses a multi-sensor algorithm responsive to rider cadence ($\omega_{\text{cadence}}$) and instantaneous crank torque ($\tau_{\text{pedal}}$). Navigating tight, punchy switchbacks draws peak assistance current (up to 85 Nm) at very low ground speeds ($v \approx 4\text{--}6\,\text{km/h}$), consuming substantial watt-hours with minimal distance accumulation.

4. **Total System Mass ($m_{\text{total}}$):**
   Garmin records rider elevation gain, but does not capture variations in rider gear, hydration packs, winter apparel, or bikepacking loads, altering gravitational work $E_{\text{climb}} = m_{\text{total}} \cdot g \cdot \Delta h$.

5. **Motor Thermal Derating & Electromechanical Efficiency:**
   Sustained steep ascents heat the motor windings and stator magnets. As copper temperature rises, winding resistance increases ($R_{\text{cu}} \propto 1 + \alpha \Delta T$), reducing motor efficiency $\eta_{\text{motor}}$ and triggering thermal throttling algorithms that alter the electrical-to-mechanical conversion ratio.

6. **BMS SoC Estimation Error vs. Precision Coulometry:**
   Consumer e-bike Battery Management Systems (BMS) estimate SoC using a combination of loaded terminal voltage curves, lookup tables, and coarse current integration—not calibrated laboratory coulometry. Cell aging, temperature-induced cell imbalance, and voltage relaxation hysteresis can cause non-linearities in the transmitted integer SoC that do not correspond to true capacity loss.

### 2.3 Collinearity & The Low SNR Regime (Calendar vs. Throughput)
Modern 21700 lithium-ion cylindrical cells (such as the Samsung 50E or LG M50LT cells powering 720 Wh packs) are rated for 500 to 1,000 full equivalent cycles before reaching an 80% State of Health threshold.

In typical consumer usage—for example, **27.0 Equivalent Full Cycles (EFC) accumulated over 18 months**:
- **Physically expected cycle fade** over 27 EFC is modest ($<2\text{--}3\%$).
- **Calendar aging** (electrolyte decomposition and solid electrolyte interphase [SEI] layer growth at ambient storage temperature) is the dominant physical degradation mechanism.
- **Collinearity:** Cumulative cycle throughput ($\text{EFC}$) and calendar time ($t$) are nearly collinear ($r > 0.95$). Including both simultaneously in the regression yields an ill-conditioned design matrix ($X^\top X$).
- **Signal-to-Noise Ratio (SNR):** Real-world route-to-route energy consumption noise is approximately $\pm 10\%$, which is several times larger than the expected true cycle degradation signal over 27 EFC.

Therefore, the degradation metrics reported by Packfade must be understood as an **Apparent Consumption-Normalized Capacity Index**—an empirical upper bound on pack health under real-world conditions—rather than an absolute electrochemical bench test.

---

## 3. Multi-Covariate Log-Linear Degradation Model

### 3.1 The Degradation Hypothesis
We model capacity retention $C(t)$ as an exponential decay process governed by time $t$ (or cumulative throughput cycle equivalents):
$$C(t) = C_0 \cdot \exp(-\beta_t \cdot t)$$

Taking natural logarithms linearizes the degradation path:
$$\ln(y_i) = \beta_0 + \beta_t \cdot t_i + \mathbf{x}_i^\top \boldsymbol{\gamma} + \epsilon_i$$

where:
- $y_i = \frac{\Delta\text{SoC}_i}{d_i}$ is the observed consumption rate.
- $t_i$ is normalized time (in elapsed years from baseline date $t_0$, or cumulative Equivalent Full Cycles $\text{EFC}_i$).
- $\beta_t$ is the degradation rate coefficient.
- $\mathbf{x}_i$ is the vector of environmental and operational confounding covariates.
- $\boldsymbol{\gamma}$ is the vector of control coefficients.
- $\epsilon_i \sim \mathcal{N}(0, \sigma_i^2)$ is the residual error.

### 3.2 Covariate Normalization Matrix
To isolate true electrochemical degradation from seasonal and behavioral variations, $\mathbf{x}_i$ controls for six critical physical factors:

$$\mathbf{x}_i = \begin{bmatrix} 
T_i \\ 
v_i \\ 
\overline{\text{SoC}}_i \\ 
f_{\text{mode5}, i} \\ 
f_{\text{mode3}, i} \\ 
f_{\text{mode2}, i} 
\end{bmatrix}$$

1. **Ambient Temperature ($T_i$ in $^\circ\text{C}$):** Lithium-ion internal resistance exhibits Arrhenius-type temperature sensitivity. Cold temperatures increase electrolyte viscosity and charge transfer resistance, increasing apparent consumption per km. We report the 10-degree temperature sensitivity:
   $$\text{Sens}_{10^\circ\text{C}} = \exp(10 \cdot \gamma_T) - 1$$
   *(Empirical result on PW-X3 dataset: $-6.7\%$ energy required per $+10^\circ\text{C}$ increase).*

2. **Moving Velocity ($v_i$ in $\text{km/h}$):** Aerodynamic drag power scales cubically with velocity ($P_{\text{aero}} \propto v^3$), meaning energy per distance scales quadratically ($E_{\text{aero}}/d \propto v^2$). Weighted moving average speed controls for ride pace.

3. **Mean Operating State of Charge ($\overline{\text{SoC}}_i$):** Open-circuit voltage (OCV) curves are non-linear. Operating primarily in the bottom 20% incurs higher resistive $I^2 R$ losses and steeper voltage sag than cruising in the 60–80% plateau.

4. **Assistance Mode Distributions ($f_{\text{mode}, i}$):** Time fractions spent in Yamaha motor assistance codes:
   - Mode 7: High / EXPW (Highest torque assistance)
   - Mode 5: High
   - Mode 3: STD (Standard)
   - Mode 2: ECO

5. **Gravitational Ascent ($h_i/d_i$ in $\text{m/km}$):** Gravitational potential energy $E_{\text{potential}} = m \cdot g \cdot \Delta h$ directly increases energy draw per horizontal kilometer.

---

## 4. Quantization Error & Weighted Least Squares (WLS)

### 4.1 Integer Quantization Noise
Garmin ANT+ LEV telemetry records battery SoC in discrete integer increments ($Q = 1\%$). An integer reading of $S\%$ represents a continuous true physical state $S^* \in [S - 0.5, S + 0.5]\%$, modeled as a uniform distribution $\mathcal{U}(-0.5, 0.5)$.

The variance of a single integer reading is:
$$\sigma_{\text{reading}}^2 = \frac{Q^2}{12} = \frac{1^2}{12} = \frac{1}{12}$$

Since start and end endpoints are independent random variables:
$$\text{Var}(\Delta\text{SoC}_i) = \text{Var}(\text{SoC}_{\text{start}, i}) + \text{Var}(\text{SoC}_{\text{end}, i}) = \frac{1}{12} + \frac{1}{12} = \frac{1}{6}$$

### 4.2 Relative Variance via the Delta Method and Environmental Process Noise
The regression dependent variable is the logarithm of the consumption rate:
$$\ln(y_i) = \ln\left(\frac{\Delta\text{SoC}_i}{d_i}\right) = \ln(\Delta\text{SoC}_i) - \ln(d_i)$$

Using the first-order Taylor expansion (the Delta Method) for the non-linear transformation $g(S) = \ln(S)$:
$$\text{Var}_{\text{quant}}\left(\ln \Delta\text{SoC}_i\right) \approx [g'(\Delta\text{SoC}_i)]^2 \cdot \text{Var}(\Delta\text{SoC}_i) = \left(\frac{1}{\Delta\text{SoC}_i}\right)^2 \cdot \frac{1}{6} = \frac{1}{6 \cdot (\Delta\text{SoC}_i)^2}$$

In addition to discrete integer quantization noise, every real-world ride experiences unmodeled environmental process noise (gusts of wind, road surface micro-roughness, subtle tire pressure changes, rider posture shifts). We model this route-level process disturbance as an independent log-scale variance:
$$\text{Var}_{\text{env}}\left(\ln y_i\right) = \sigma_{\text{env}}^2 = 0.05^2$$

This $0.05^2$ term corresponds to an assumed **5% relative coefficient of variation ($\sigma_{\text{relative}} = 0.05$)** in energy consumption per kilometer.

Total observation variance is therefore:
$$\text{Var}\left(\ln y_i\right) = \text{Var}_{\text{env}} + \text{Var}_{\text{quant}} = 0.05^2 + \frac{1}{6 \cdot (\Delta\text{SoC}_i)^2}$$

### 4.3 Why the Environmental Floor ($0.05^2$) Is Indispensable
Without the $0.05^2$ term:
$$\lim_{\Delta\text{SoC}_i \to \infty} \text{Var}_{\text{quant}} = 0 \implies \lim_{\Delta\text{SoC}_i \to \infty} w_i = \infty$$

A pure quantization model would assign arbitrarily massive weights to long discharge rides, treating an 80% discharge ride as infinitely precise compared to shorter rides. 

The $5\%$ relative noise floor caps the maximum possible observation weight at:
$$w_{\max} = \frac{1}{0.05^2} = 400$$

This correctly captures the physical reality: even over a 100% full discharge ride, route-level environmental variance cannot be averaged out below the ~5% empirical threshold.

### 4.4 Optimal Weight Formulation
Each observation is weighted by the inverse of its total composite variance:
$$w_{\text{base}, i} = \frac{1}{0.05^2 + \frac{1}{6 \cdot (\Delta\text{SoC}_i)^2}}$$

- **Short rides (e.g. $\Delta\text{SoC} = 4\%$):** $w_i \approx 77.4$ (heavily downweighted due to $1\%$ quantization dominance).
- **Long rides (e.g. $\Delta\text{SoC} = 80\%$):** $w_i \approx 396.0$ ($5.1\times$ higher statistical influence, precision dominated by long discharge integration).

---

## 5. Robust Huber M-Estimation

Real-world rides encounter non-Gaussian external disturbances: heavy headwinds, mud, cargo weight, or soft tire pressure. Standard OLS is vulnerable to these heavy-tailed residuals.

We apply **Iteratively Reweighted Least Squares (IRLS)** with Huber's robust objective function:
$$\rho(u) = \begin{cases} 
\frac{1}{2} u^2 & \text{for } |u| \le k \\ 
k |u| - \frac{1}{2} k^2 & \text{for } |u| > k 
\end{cases}$$

where $k = 1.345$ achieves 95% asymptotic efficiency on Gaussian data while bounding influence on gross outliers.

### 5.1 Huber IRLS Algorithm
1. Compute weighted least-squares coefficients: $\boldsymbol{\beta}^{(0)} = (\mathbf{X}^\top \mathbf{W}_{\text{base}} \mathbf{X})^{-1} \mathbf{X}^\top \mathbf{W}_{\text{base}} \mathbf{y}$.
2. Compute residuals: $e_i = y_i - \mathbf{x}_i^\top \boldsymbol{\beta}$.
3. Estimate scale using the Median Absolute Deviation (MAD):
   $$s = 1.4826 \cdot \text{median}\left(|e_i - \text{median}(e)|\right)$$
4. Standardize residuals: $u_i = \frac{|e_i|}{\max(s, 10^{-4})}$.
5. Calculate Huber weights:
   $$w_{\text{Huber}, i} = \begin{cases} 1 & u_i \le 1.345 \\ \frac{1.345}{u_i} & u_i > 1.345 \end{cases}$$
6. Update total diagonal weights: $\mathbf{W}^{(m+1)} = \mathbf{W}_{\text{base}} \odot \mathbf{W}_{\text{Huber}}$.
7. Re-solve weighted system until convergence (4 iterations guarantee convergence in practice).

---

## 6. Clustered Block Bootstrap for Multi-Stage Rides

Standard bootstrap resampling assumes identically and independently distributed (i.i.d.) observations. However, multiple rides performed on the same calendar day (e.g. morning commute and evening return) share identical atmospheric conditions, battery aging state, and bike configurations.

Treating them as independent would lead to pseudo-replication and artificially narrow confidence intervals.

### 6.1 Date-Clustered Resampling
1. Partition observations into $G$ disjoint date clusters: $\mathcal{G}_1, \mathcal{G}_2, \dots, \mathcal{G}_G$ where $\mathcal{G}_d = \{i \mid \text{Date}(i) = d\}$.
2. For each bootstrap iteration $b \in \{1, \dots, B\}$:
   - Sample $G$ clusters **with replacement** from $\{\mathcal{G}_1, \dots, \mathcal{G}_G\}$.
   - Concatenate all ride indices contained in the sampled clusters: $\mathcal{I}_b = \bigcup_{g \in \text{Sample}} \mathcal{G}_g$.
   - Re-fit the Huber-WLS regression on $\mathbf{X}[\mathcal{I}_b], \mathbf{y}[\mathcal{I}_b]$.
   - Extract the bootstrapped degradation slope $\beta_t^{(b)}$.
3. Construct the 95% empirical percentile confidence intervals:
   $$\beta_t \in \left[\mathcal{Q}_{0.025}\left(\beta_t^{(b)}\right), \, \mathcal{Q}_{0.975}\left(\beta_t^{(b)}\right)\right]$$
4. Translate to Apparent Capacity Index confidence bounds:
   $$\text{Index}_{\text{end}} \in \left[100 \cdot \exp\left(-\mathcal{Q}_{0.975} \cdot t_{\text{end}}\right), \, 100 \cdot \exp\left(-\mathcal{Q}_{0.025} \cdot t_{\text{end}}\right)\right]$$

---

## 7. Dual-Time Formulations: Calendar vs. Cycle Aging

Degradation occurs through two coupled electrochemical mechanisms:
1. **Calendar Aging (SEI Layer Growth):** Passivation layer growth on graphite anodes, driven by time, storage temperature, and average SoC.
2. **Cycle Aging (Mechanical Degradation):** Active material crack formation and lithium stripping/plating from volumetric expansion during charge/discharge cycles.

To evaluate both mechanisms, the pipeline fits two parallel time baselines:

| Dimension | Variable $t$ | Definition | Typical Measured Rate |
| :--- | :--- | :--- | :--- |
| **Calendar Aging** | $t_{\text{years}}$ | $\frac{\text{Date}_i - \text{Date}_0}{365.25 \times 86400\,\text{s}}$ | $\beta_{\text{annual}} \approx 0.0586\,\text{yr}^{-1}$ (Apparent Capacity: $91.7\%$) |
| **Cycle Aging** | $\text{EFC}$ | $\frac{1}{100} \sum_{k=1}^i \Delta\text{SoC}_k$ | $\beta_{\text{cycle}} \approx 0.00586\,\text{EFC}^{-1}$ (Apparent Capacity: $85.4\%$) |

*(Note: As discussed in Section 2.3, because calendar time and cycle throughput are highly collinear over 27 EFC, these rates reflect empirical bounds rather than decoupled orthogonal parameters).*

---

## 8. Mathematical Rectification of ANT+ LEV Fallbacks

### 8.1 Same-Side Excursion Invariant
When an e-bike motor controller auto-powers down during a stop, the Garmin Edge ANT+ LEV receiver defaults to an uninitialized placeholder value of $50\%$ for 1 to 5 seconds upon reconnection.

To distinguish this digital artifact from genuine battery discharge through 50%:

$$\begin{aligned}
\text{Genuine Passage:} &\quad (\text{SoC}_{t-1} - 50) \cdot (\text{SoC}_{t+1} - 50) \le 0 \\
\text{Glitch Excursion:} &\quad (\text{SoC}_{t-1} - 50) \cdot (\text{SoC}_{t+1} - 50) > 0
\end{aligned}$$

### 8.2 Decision Rule
A run of contiguous $50\%$ readings from index $a$ to $b$ is classified as an artifact and masked if and only if:
1. **Duration Constraint:** $t_b - t_a \le 5\,\text{s}$.
2. **Interior Boundary:** $a > 0$ and $b < N - 1$.
3. **Same-Side Condition:** $(\text{SoC}_{a-1} - 50) \cdot (\text{SoC}_{b+1} - 50) > 0$.
4. **Excursion Amplitude:** $|\text{SoC}_{a-1} - 50| \ge 2\,\text{pp}$ and $|\text{SoC}_{b+1} - 50| \ge 2\,\text{pp}$.
5. **State Consistency:** $|\text{SoC}_{a-1} - \text{SoC}_{b+1}| \le 2\,\text{pp}$.

Filtering these transient samples recovers continuous monotonic discharge while eliminating artificial $+4\text{ to }+48\text{ pp}$ steps that would otherwise cause erroneous ride rejection.
