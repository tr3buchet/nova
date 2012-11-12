# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2012 OpenStack LLC.
# All Rights Reserved.
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
#    under the License.

from sqlalchemy import Index, MetaData, Table
from sqlalchemy.exc import IntegrityError


def upgrade(migrate_engine):
    return
    meta = MetaData()
    meta.bind = migrate_engine

    # Based on key_pair_get
    # from: nova/db/sqlalchemy/api.py
    t = Table('key_pairs', meta, autoload=True)
    i = Index('key_pair_user_id_name_idx',
              t.c.user_id, t.c.name)
    try:
        i.create(migrate_engine)
    except IntegrityError:
        pass


def downgrade(migrate_engine):
    return
    meta = MetaData()
    meta.bind = migrate_engine

    t = Table('key_pairs', meta, autoload=True)
    i = Index('key_pair_user_id_name_idx',
              t.c.user_id, t.c.name)
    i.drop(migrate_engine)
