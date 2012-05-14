#!/usr/bin/env python
# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2012 Rackspace
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
"""
Script to verify that a public-key file is in a format PyCrypto understands.

This is useful for verifying public-keys related to the RAX Password Encyrption
module.

     ./tools/crypto/validate_public_key.py /tmp/keyfile.pub
"""
import os
import sys

# If ../nova/__init__.py exists, add ../ to Python search path, so that
# it will override what happens to be installed in /usr/(local/)lib/python...
POSSIBLE_TOPDIR = os.path.normpath(os.path.join(os.path.abspath(sys.argv[0]),
                                   os.pardir,
                                   os.pardir,
                                   os.pardir))
if os.path.exists(os.path.join(POSSIBLE_TOPDIR, 'nova', '__init__.py')):
    sys.path.insert(0, POSSIBLE_TOPDIR)

from nova import utils


def usage():
    print "validate_public_key.py <path-to-public-key>"
    sys.exit(1)


def main():
    if len(sys.argv) < 2:
        usage()

    filename = sys.argv[1]
    public_key = open(filename, 'r').read()
    test_data = 'blah'
    try:
        utils.encrypt_rsa(public_key, test_data)
        print "Success"
    except ValueError:
        print "Key in wrong format"
        sys.exit(1)


if __name__ == "__main__":
    main()
