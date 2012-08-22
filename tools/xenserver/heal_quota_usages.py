#!/usr/bin/env python

import sys

from nova import config
import nova.context
from nova.db.sqlalchemy import api
from nova.db.sqlalchemy import models


def fetch_quota_data(context, session, project_id, lock=False):
    # Fetch (and potentially lock) quota usages for project
    usages = {}
    query = api.model_query(context, models.QuotaUsage, session=session).\
                filter_by(project_id=project_id)
    if lock:
        query = query.with_lockmode('update')
    for quotausage in query.all():
        usages[quotausage.resource] = quotausage

    # Fetch quota reservations for project
    query = api.model_query(context, models.Reservation, session=session).\
                filter_by(project_id=project_id)
    reserved = {'instances': 0, 'cores': 0, 'ram': 0}
    for reservation in query.all():
        if reservation.resource not in reserved:
            print '  unknown resource %r' % reservation.resource
            reserved[reservation.resource] = 0

        reserved[reservation.resource] += reservation.delta

    # Fetch instances for project
    instances = api.instance_get_all_by_project(context, project_id)

    in_use = {'instances': 0, 'cores': 0, 'ram': 0}
    for instance in instances:
        in_use['instances'] += 1
        in_use['cores'] += instance['vcpus']
        in_use['ram'] += instance['memory_mb']

    return usages, reserved, in_use


def compare_total(usage, counts, key, resource, heal=False):
    db = usage and getattr(usage, key) or 0
    count = counts[resource]
    if db != count:
        print '  %s %s mismatch (%d db != %d counted)' % (key, resource,
                                                          db, count)
        if usage:
            if heal:
                setattr(usage, key, count)
        else:
            print '  !! no usage row?'
        return False if usage else True

    return True


def check(context, project_id):
    print project_id

    session = api.get_session()
    with session.begin():
        usages, reserved, in_use = fetch_quota_data(context, session,
                                                    project_id)

        usage = usages.get('instances')
        compare_total(usage, reserved, 'reserved', 'instances')
        compare_total(usage, in_use, 'in_use', 'instances')

        usage = usages.get('cores')
        compare_total(usage, reserved, 'reserved', 'cores')
        compare_total(usage, in_use, 'in_use', 'cores')

        usage = usages.get('ram')
        compare_total(usage, reserved, 'reserved', 'ram')
        compare_total(usage, in_use, 'in_use', 'ram')


def heal(context, project_id):
    print project_id

    session = api.get_session()
    with session.begin():
        usages, reserved, in_use = fetch_quota_data(context, session,
                                                    project_id,
                                                    lock=True)

        usage = usages.get('instances')
        compare_total(usage, reserved, 'reserved', 'instances',
                      heal=True)
        compare_total(usage, in_use, 'in_use', 'instances', heal=True)

        usage = usages.get('cores')
        compare_total(usage, reserved, 'reserved', 'cores', heal=True)
        compare_total(usage, in_use, 'in_use', 'cores', heal=True)

        usage = usages.get('ram')
        compare_total(usage, reserved, 'reserved', 'ram', heal=True)
        compare_total(usage, in_use, 'in_use', 'ram', heal=True)


def main():
    args = config.parse_args(sys.argv)

    context = nova.context.get_admin_context()

    command = args[1] if len(args) > 1 else 'check'
    if command == 'check':
        func = check
    elif command == 'heal':
        func = heal
    else:
        sys.exit('Unknown command %r' % command)

    if len(args) > 2:
        func(context, args[2])
    else:
        for quotausage in api.model_query(context, models.QuotaUsage).\
                              group_by(models.QuotaUsage.project_id).\
                              all():
            func(context, quotausage.project_id)


if __name__ == "__main__":
    main()
