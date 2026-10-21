# Roadmap

## Current state (0.2.0)

Feature-complete for the original goal: a Synology NAS monitored through the native net-snmp tools, hardware detected
dynamically, modular code, documented, tested against simulated DS920+ / DS218+ data and a real `snmpd`.
**Not yet verified on real DSM hardware.** That is the main gap, and the next milestone.

## 0.2.x - verify on real hardware

Fix what real NAS units show differently from the Synology MIB guide and the fixtures. Open points, in the order of
how likely they are to matter:

- [ ] UPS: real output format of the battery / load / runtime values (Opaque float vs integer) and the runtime OID,
      which was taken from the previous plugin and is not in the guide
- [ ] Volumes: `hrStorageDescr` text for volumes, USB / eSATA shares and the system partition on DSM 6.2, 7.0, 7.2, 7.3
- [ ] Drive numbering with SSD cache / NVMe and with expansion units (does a unit's drive appear twice?)
- [ ] How DSM 7.3 reports drives that are not on Synology's compatibility list
- [ ] SNMPv3 against DSM (auth / privacy protocol combinations DSM offers)
- [ ] Add real captured models as fixtures (DS218+, DS1525+, a rack unit) via `tools/capture_mib.sh`
- [ ] 2025 and later models: check for new or changed MIB objects

## 0.3 - fewer process starts, more coverage

- [ ] Batch several walks into one `snmpbulkwalk` call (it accepts several OIDs), to cut the first-poll call count
- [ ] Pass SNMP credentials through a private (mode 0600) net-snmp configuration file instead of command-line arguments,
      so they no longer show up in `ps` (see SECURITY.md)
- [x] Configurable fast/slow polling schedules (0.2.2: CPU/memory and fast metrics on the main interval; disks, storage pools, volume capacity, SMART and optional hardware on the slow interval).
- [ ] Areas not covered yet, only where the NAS exposes them over SNMP and a real dump is available: iSCSI LUNs,
      high-availability cluster state, NFS / SMB statistics
- [ ] Domoticz selector switches for the "DSM update" and "disk summary" states, if requested

## Release and packaging

- [ ] Publish the first GitHub release (`v0.2.0`) and check that the plugin manager sees it
      (release tags are plain numbers, see `checklist.md`)
- [ ] Verify the plugin on a second Domoticz host (the `deploy.sh --host` workflow)

## Ideas, not planned

- SNMP traps: polling does the job and needs no listener on the Domoticz host
- Changing anything on the NAS (`snmpset`, DSM web API): the plugin is deliberately read-only
- A Python SNMP library as an alternative backend: the point of this project is to have none
