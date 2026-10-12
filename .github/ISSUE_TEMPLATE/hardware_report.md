---
name: My NAS model / DSM version (works, partly works, missing devices)
about: Report how the plugin behaves on your hardware, so the compatibility notes can be completed
title: "[hardware] <model> <DSM version>"
labels: hardware
---

The plugin has only been tested against simulated data so far, so reports from real NAS units are very welcome,
**including "everything works"**.

**NAS**

- Model (e.g. DS1525+) and DSM version:
- Number of bays / drives installed, SSD cache, expansion units, UPS attached:
- Volumes / storage pools (SHR, RAID 1, Btrfs, ...):

**Result of `python3 -m synoSNMP NAS_IP --rates 10`** (remove serial numbers)

```

```

**What is right, what is missing or wrong?** (drive count, temperatures, volume sizes, network ports, UPS values, ...)

**Optional:** the output of `tools/capture_mib.sh NAS_IP` as an attachment, with serial numbers, host name and MAC
addresses removed. This is what makes it possible to add a model as a test fixture.
