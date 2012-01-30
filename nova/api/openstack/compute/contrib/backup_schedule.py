# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2011 OpenStack LLC.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License

"""Backup Schedule extension."""

import webob.exc

from nova.api.openstack import extensions
from nova.api.openstack import wsgi
from nova.api.openstack import xmlutil
from nova import flags
from nova.openstack.common import cfg

try:
    import tempo.client
    tempo_available = True
except ImportError:
    tempo_available = False
    LOG.warn(_("Unable to import tempo, backup schedules API extension will"
               " not be available."))

DAYS = ['SUNDAY', 'MONDAY', 'TUESDAY', 'WEDNESDAY', 'THURSDAY', 'FRIDAY',
        'SATURDAY']

NAMESPACE = "http://docs.openstack.org/ext/backup-schedule/api/v1.1"
NSMAP = {None: NAMESPACE}

backup_schedule_opts = [
    cfg.StrOpt('tempo_url',
               default='http://127.0.0.1:8080',
               help='URL to pass to connect to Tempo with.'),
    cfg.IntOpt('weekly_backup_start_hour',
               default=0,
               help='Hour that weekly backups should start (0..23)'),
    cfg.IntOpt('weekly_backup_start_minute',
               default=0,
               help='Minutes that weekly backups should start on'
                    ' (0..59)'),
    cfg.IntOpt('daily_backup_start_minute',
               default=0,
               help='Minutes that daily backups should start on (0..59)')
]

FLAGS = flags.FLAGS
FLAGS.register_opts(backup_schedule_opts)


def make_hour_spec(start_hour):
    """Return an hour_spec given the start_hour.

    Ex. 0 -> H_0000_2000, 22 -> H_2200_0000
    """
    if not (0 <= start_hour <= 22):
        raise ValueError("start hour must be between 0 and 22 inclusive")
    elif start_hour % 2 != 0:
        raise ValueError("must start on an even hour")

    end_hour = (start_hour + 2) % 24
    return "_".join(["H", "%02d00" % start_hour, "%02d00" % end_hour])


def make_recurrence(minute, hour, day_of_week):
    """Return abbreviated form of recurrence.

    Rather than use a full crontab entry, Tempo currently uses an abbreviated
    form of specifying the cron schedule, skipping the day of month and month
    fields.
    """
    return ' '.join(map(str, [minute, hour, day_of_week]))


def parse_hour_spec(hour_spec):
    """Hour spec is a two-hour time-window that dictates when an hourly backup
    should kick off.

    Ex. h_0000_0200 is a backup off that kicks off sometime between midnight
    and 2am GMT.
    """
    prefix, start_window, end_window = hour_spec.split('_')
    return int(start_window) / 100


def parse_day_str(day_str):
    """Return the day of the week given a day as a string.

    Ex. Sunday -> 0, Monday -> 1, ...
    """
    return DAYS.index(day_str.upper())


class BackupScheduleTemplate(xmlutil.TemplateBuilder):
    def construct(self):
        root = xmlutil.TemplateElement('backupSchedule',
                                       selector='backupSchedule')
        root.set('enabled')
        root.set('weekly')
        root.set('daily')
        return xmlutil.MasterTemplate(root, 1, nsmap=NSMAP)


class BackupScheduleController(object):
    def __init__(self, fake=False):
        if tempo_available:
            self.tempo_client = tempo.client.TempoClient(FLAGS.tempo_url)
        else:
            raise ImportError(_("tempo.client unable to be imported."))

    @wsgi.serializers(xml=BackupScheduleTemplate)
    def index(self, req, server_id):
        """Retrieve a backup schedule."""

        daily_backup, weekly_backup = self._get_tempo_backups(server_id)

        daily = 'DISABLED'
        weekly = 'DISABLED'
        enabled = False
        rotation = 0

        if daily_backup:
            hour = int(daily_backup['recurrence'].split(' ')[1])
            daily = make_hour_spec(hour)
            enabled = True
            rotation = int(daily_backup['params'].get('rotation', '0'))

        if weekly_backup:
            day_of_week = int(weekly_backup['recurrence'].split(' ')[2])
            weekly = DAYS[day_of_week]
            enabled = True
            rotation = int(weekly_backup['params'].get('rotation', '0'))

        backup_schedule = dict(enabled=enabled,
                               weekly=weekly,
                               daily=daily,
                               rotation=rotation)

        return {"backupSchedule": backup_schedule}

    @wsgi.serializers(xml=BackupScheduleTemplate)
    @wsgi.deserializers(xml=BackupScheduleTemplate)
    def create(self, req, server_id, body):
        """Create or update a backup schedule."""
        schedule = body['backupSchedule']

        # clear old schedule out (if needed)
        self._disable_backups(server_id)

        # recreate new one
        if schedule['enabled']:
            try:
                params = dict(rotation=schedule['rotation'])
            except KeyError:
                params = None

            # weekly
            day_str = schedule.get('weekly', 'DISABLED').upper()
            if day_str != 'DISABLED':
                recurrence = make_recurrence(
                    minute=FLAGS.weekly_backup_start_minute,
                    hour=FLAGS.weekly_backup_start_hour,
                    day_of_week=parse_day_str(day_str))

                self.tempo_client.task_create(
                    'weekly_backup', server_id, recurrence, params=params)

            # daily
            hour_spec = schedule.get('daily', 'DISABLED').upper()
            if hour_spec != 'DISABLED':
                recurrence = make_recurrence(
                    minute=FLAGS.daily_backup_start_minute,
                    hour=parse_hour_spec(hour_spec),
                    day_of_week='*')

                self.tempo_client.task_create(
                    'daily_backup', server_id, recurrence, params=params)

        return webob.exc.Response(status_int=204)

    def delete_all(self, req, server_id):
        """Delete a backup schedule."""
        self._disable_backups(server_id)
        return webob.exc.Response(status_int=204)

    def _disable_backups(self, server_id):
        daily_backup, weekly_backup = self._get_tempo_backups(server_id)

        if daily_backup:
            self.tempo_client.task_delete(daily_backup['uuid'])

        if weekly_backup:
            self.tempo_client.task_delete(weekly_backup['uuid'])

    def _get_tempo_backups(self, server_id):
        # TODO(sirp): use tempo query-by-instance when that becomes available.
        # (returning all tasks in the system is wildly inefficient)
        tasks = self.tempo_client.task_get_all()

        daily_backup = None
        weekly_backup = None
        for task in tasks:
            if task['instance_uuid'] == server_id:
                action = task['action']
                if action == 'daily_backup':
                    daily_backup = task
                elif action == 'weekly_backup':
                    weekly_backup = task

        return daily_backup, weekly_backup


class Backup_schedule(extensions.ExtensionDescriptor):
    """Backup schedule support for instances."""

    name = "Backup_schedule"
    alias = "rax-backup-schedule"
    namespace = NAMESPACE
    updated = "2011-08-18T00:00:00+00:00"

    def get_resources(self):
        def custom_routes_fn(mapper, wsgi_resource):
            mapper.connect("backup_schedule",
                           "/{project_id}/servers/{server_id}/backup_schedule",
                           controller=wsgi_resource,
                           action='delete_all',
                           conditions={"method": ['DELETE']})

        return [extensions.ResourceExtension(
                    'backup_schedule',
                    BackupScheduleController(),
                    parent={'member_name': 'server',
                            'collection_name': 'servers'},
                    custom_routes_fn=custom_routes_fn)]
