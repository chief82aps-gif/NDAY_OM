# Work Summary Tool (WST) Rules

## Purpose
Define ingest and audit rules for the weekly Work Summary Tool zip. These files are the operational truth used to validate invoices and performance.

## Source File
- NDAY Weekly Report-YYYY-MM-DD.zip

## Files Inside the ZIP
### 1) Delivered Packages Report
File: NDAY Delivered Packages Report-YYYY-MM-DD.csv

**What it is**
- Daily totals of delivered packages and pickup packages (company aggregate).

**Store**
- Date
- Station
- DSP short code
- Package count
- Package type

**Audit ideas**
- Compare totals vs Cortex and DOP aggregates to validate DCR.
- Match pickup package totals vs Variable Invoice pickup line items.

---

### 2) Service Details Report
File: NDAY Service Details Report-YYYY-MM-DD.csv

**What it is**
- Daily route-level work summary: driver, route, service type, duration, log in/out, distance, shipments, excluded flag.

**Store**
- Date
- Station
- DSP short code
- Delivery associate name
- Route code
- Service type
- Planned duration
- Log in / log out
- Total distance planned / allowance
- Shipments delivered / returned
- Pickup packages
- Excluded flag

**Audit ideas**
- Weekly exclusion report (any Excluded = yes).
- Sanity check against Variable Invoice summary lines (routes and service types).
- Match route counts vs DOP/Cortex and route sheets.

---

### 3) Training Weekly Report
File: NDAY Training Weekly Report-YYYY-MM-DD.csv

**What it is**
- Training sessions by delivery associate and chapter; includes DSP payment eligibility.

**Store**
- Assignment date
- Payment date
- Station
- DSP short code
- Delivery associate
- Service type (Training)
- Course name
- DSP payment eligible (yes/no)

**Audit ideas**
- Weekly report of DSP payment eligible = no (date + driver).
- Training days count should reconcile with Variable Invoice training line items.
- Standard training day = 8 hours (lower rate than delivery routes).

---

### 4) Unplanned Delay Weekly Report
File: NDAY Unplanned Delay Weekly Report-YYYY-MM-DD.csv

**What it is**
- Delay reasons and total minutes.

**Store**
- Date
- Station
- DSP short code
- Delay reason
- Total delay minutes
- Impacted routes
- Notes

**Audit ideas**
- Sum minutes, convert to hours, compare against Unplanned Delay invoice line items.

---

### 5) Weekly Report
File: NDAY Weekly Report-YYYY-MM-DD.csv

**What it is**
- High-level summary of routes by date, service type, duration, distance, cancels, completed routes.

**Store**
- Date
- Station
- DSP short code
- Service type
- Planned duration
- Total distance planned / allowance
- Planned distance unit
- AMZL late cancel
- DSP late cancel
- Quick coverage accepted
- Completed routes

**Audit ideas**
- Alert on any DSP late cancel (must have justification).
- Compare completed routes vs Variable Invoice route block line items.
- Reconcile totals vs DOP/Cortex and route sheets.

---

## Cross-File Variance Checks (Future)
- WST total routes vs DOP/Cortex totals vs Invoice totals should net to zero variance.
- Deliveries and pickups should align with invoice per-shipment payments.
- Exclusions should be tracked and removed from invoice expectation totals.
