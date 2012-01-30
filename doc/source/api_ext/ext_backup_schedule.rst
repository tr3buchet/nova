===================================
About The Backup Schedule Extension
===================================

This documents the REST API provided by Rackspace's Nova backup scheduler
extension. The main topics covered are:

  - How to enable/disable daily backups for an instance
  - How to enable/disable weekly backups for an instance
  - How to specify how many extra backups are kept (backup rotation)

Backup Schedule Extension Overview
==================================

Name
	Backup Schedule

Namespace
    http://docs.openstack.org/ext/backup-schedule/api/v1.1

Alias
	rax-backup-schedule

Contact
    Rick Harris <rick.harris@rackspace.com>

Status
	Released

Extension Version
	v1.0 (2012-03-19)

Dependencies
	Compute API 1.1

Doc Link (PDF)
	http://

Doc Link (WADL)
	http://

Short Description
    This extension enables backup schedule listing, creation, and deletion through a REST API.


Sample Query Responses
======================

Get Backup Schedule
-------------------

Request Line::

  GET /servers/<INSTANCE UUID>/backup_schedule

Response Body::

  {
      "backupSchedule": {
          "daily": "DISABLED",
          "enabled": true,
          "rotation": 2,
          "weekly": "SUNDAY"
      }
  }


Update Backup Schedule
----------------------

Request Line::

  PUT /servers/<INSTANCE UUID>/backup_schedule

Request Body::

  {
      "backupSchedule": {
          "daily": "H_0600_0800",
          "enabled": true,
          "rotation": 2,
          "weekly": "SUNDAY"
      }
  }


Delete Backup Schedule
----------------------

Request Line::

  DELETE /servers/<INSTANCE UUID>/backup_schedule


Parameter Values
================


enabled (boolean)
-----------------

Determines whether the current backup schedule is active.

Disabling a backup schedule is slightly different than deleting a backup
schedule. If you delete a backup schedule, the scheduling parameters are lost
whereas if you disable it, the scheduling parameters are preserved for later
use.


rotation (integer)
------------------

Determines the number of recent backups we should keep for an instance. Zero
indicates that we don't keep any extra backups.

Backups are rotated only when the current backup completes successfully, so
while a backup is being created, we will have N+1 backups visible. As an
example::

  # rotation=1

  # Initially we have two backups (most-recent plus one extra)
  Backup1 ACTIVE
  Backup2 ACTIVE

  # We create a new backup which is in the SAVING state
  Backup1 ACTIVE
  Backup2 ACTIVE
  Backup3 SAVING

  # Backup3 finishes so we rotate out Backup1 to keep only one extra backup
  Backup2 ACTIVE
  Backup3 ACTIVE


daily (string)
--------------

Determines whether a daily backup should be created and if so, when it should
be created.

Daily backup times are specified by providing a two-hour time window for when
the instance should backup. This window is given in 24-hour time relative to
UTC in the format::

  H_<START TIME>_<END TIME>


For example, if you would like your backup to begin sometime between 10pm and
midnight UTC, you pass in "H_2200_0000".

If the value `DISABLED` is passed, no daily backup wil be created.


Valid values for `daily` are::

  ['DISABLED',
   'H_0000_0200', 'H_0200_0400', 'H_0400_0600',
   'H_0600_0800', 'H_0800_1000', 'H_1000_1200',
   'H_1200_1400', 'H_1400_1600', 'H_1600_1800',
   'H_1800_2000', 'H_2000_2200', 'H_2200_0000']


weekly (string)
---------------

Determines whether a weekly backup should be created and if so, when it should
be created.

Weekly backups occur on a particular day of the week, so to specify which day,
pass in the uppper-case name for that day. For example, to indicate that
you would like your weekly backup to occur on Monday, you would pass in
'MONDAY'.

If the value `DISABLED` is passed, no weekly backup wil be created.

Valid values for `weekly` are::

  ['DISABLED', 'SUNDAY', 'MONDAY', 'TUESDAY', 'WEDNESDAY',
   'THURSDAY', 'FRIDAY', 'SATURDAY']


Document Change History
=======================

============= =====================================
Revision Date Summary of Changes
2012-03-19    Initial draft
============= =====================================
