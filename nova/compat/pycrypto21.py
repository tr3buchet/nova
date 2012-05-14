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
"""Monkey-patches PyCrypto 2.1 to add `importKey`."""

import base64

from Crypto.PublicKey import RSA
from Crypto.Util import number


def monkey_patch():
    RSA.RSAImplementation.importKey = importKey
    RSA.RSAImplementation._importKeyDER = _importKeyDER
    RSA.importKey = RSA.RSAImplementation().importKey
    RSA.__all__.append('importKey')


# Code copied from
#
# https://github.com/dlitz/pycrypto/blob/
#       0f45878cef67965dc39144b5536368d934d61589/lib/Crypto/PublicKey/RSA.py

def _importKeyDER(self, externKey):
    der = DerSequence()
    der.decode(externKey, True)
    if len(der) == 9 and der.hasOnlyInts() and der[0] == 0:
        # ASN.1 RSAPrivateKey element
        del der[6:8]    # Remove d mod (p-1) and d mod (q-1)
        del der[0]  # Remove version
        return self.construct(der[:])
    if len(der) == 2:
        # ASN.1 SubjectPublicKeyInfo element
        if (der[0] ==
             '\x30\x0D\x06\x09\x2A\x86\x48\x86\xF7\x0D\x01\x01\x01\x05\x00'):
            bitmap = DerObject()
            bitmap.decode(der[1], True)
            if bitmap.typeTag == '\x03' and bitmap.payload[0] == '\x00':
                der.decode(bitmap.payload[1:], True)
                if len(der) == 2 and der.hasOnlyInts():
                    return self.construct(der[:])
    raise ValueError("RSA key format is not supported")


def importKey(self, externKey):
    """Import an RSA key (public or private half).

    externKey:  the RSA key to import, encoded as a string.
            The key can be in DER (PKCS#1) or in unencrypted
            PEM format (RFC1421).
    """
    if externKey.startswith('-----'):
        # This is probably a PEM encoded key
        lines = externKey.replace(" ", '').split()
        der = base64.b64decode(''.join(lines[1:-1]))
        return self._importKeyDER(der)
    if externKey[0] == '\x30':
        # This is probably a DER encoded key
        return self._importKeyDER(externKey)
    raise ValueError("RSA key format is not supported")

# Code copied from
#
# https://github.com/dlitz/pycrypto/blob/
#   0f45878cef67965dc39144b5536368d934d61589/lib/Crypto/Util/asn1.py


class DerObject(object):
    typeTags = {'SEQUENCE': '\x30', 'BIT STRING': '\x03', 'INTEGER': '\x02'}

    def __init__(self, ASN1Type=None):
        self.typeTag = self.typeTags.get(ASN1Type, ASN1Type)
        self.payload = ''

    def _lengthOctets(self, payloadLen):
        '''
        Return an octet string that is suitable for the BER/DER
        length element if the relevant payload is of the given
        size (in bytes).
        '''
        if payloadLen > 127:
            encoding = number.long_to_bytes(payloadLen)
            return chr(len(encoding) + 128) + encoding
        return chr(payloadLen)

    def encode(self):
        return self.typeTag + self._lengthOctets(
            len(self.payload)) + self.payload

    def _decodeLen(self, idx, str):
        '''
        Given a string and an index to a DER LV,
        this function returns a tuple with the length of V
        and an index to the first byte of it.
        '''
        length = ord(str[idx])
        if length <= 127:
            return (length, idx + 1)
        else:
            payloadLength = number.bytes_to_long(
                str[idx + 1:idx + 1 + (length & 0x7F)])
            if payloadLength <= 127:
                raise ValueError("Not a DER length tag.")
            return (payloadLength, idx + 1 + (length & 0x7F))

    def decode(self, input, noLeftOvers=False):
        try:
            self.typeTag = input[0]
            if (ord(self.typeTag) & 0x1F) == 0x1F:
                raise ValueError("Unsupported DER tag")
            (length, idx) = self._decodeLen(1, input)
            if noLeftOvers and len(input) != (idx + length):
                raise ValueError("Not a DER structure")
            self.payload = input[idx:idx + length]
        except IndexError:
            raise ValueError("Not a valid DER SEQUENCE.")
        return idx + length


class DerInteger(DerObject):
    def __init__(self, value=0):
        DerObject.__init__(self, 'INTEGER')
        self.value = value

    def encode(self):
        self.payload = number.long_to_bytes(self.value)
        if ord(self.payload[0]) > 127:
            self.payload = '\x00' + self.payload
        return DerObject.encode(self)

    def decode(self, input, noLeftOvers=False):
        tlvLength = DerObject.decode(self, input, noLeftOvers)
        if ord(self.payload[0]) > 127:
            raise ValueError("Negative INTEGER.")
        self.value = number.bytes_to_long(self.payload)
        return tlvLength


class DerSequence(DerObject):
    def __init__(self):
        DerObject.__init__(self, 'SEQUENCE')
        self._seq = []

    def __delitem__(self, n):
        del self._seq[n]

    def __getitem__(self, n):
        return self._seq[n]

    def __setitem__(self, key, value):
        self._seq[key] = value

    def __setslice__(self, i, j, sequence):
        self._seq[i:j] = sequence

    def __delslice__(self, i, j):
        del self._seq[i:j]

    def __len__(self):
        return len(self._seq)

    def append(self, item):
        return self._seq.append(item)

    def hasOnlyInts(self):
        if not self._seq:
            return False
        for item in self._seq:
            if not isinstance(item, (int, long)):
                return False
        return True

    def encode(self):
        '''
        Return the DER encoding for the ASN.1 SEQUENCE containing
        the non-negative integers and longs added to this object.
        '''
        self.payload = ''
        for item in self._seq:
            if isinstance(item, (long, int)):
                self.payload += DerInteger(item).encode()
            elif isinstance(item, basestring):
                self.payload += item
            else:
                raise ValueError("Trying to DER encode an unknown object")
        return DerObject.encode(self)

    def decode(self, input, noLeftOvers=False):
        '''
        This function decodes the given string into a sequence of
        ASN.1 objects. Yet, we only know about unsigned INTEGERs.
        Any other type is stored as its rough TLV. In the latter
        case, the correctectness of the TLV is not checked.
        '''
        self._seq = []
        try:
            tlvLength = DerObject.decode(self, input, noLeftOvers)
            if self.typeTag != self.typeTags['SEQUENCE']:
                raise ValueError("Not a DER SEQUENCE.")
            # Scan one TLV at once
            idx = 0
            while idx < len(self.payload):
                typeTag = self.payload[idx]
                if typeTag == self.typeTags['INTEGER']:
                    newInteger = DerInteger()
                    idx += newInteger.decode(self.payload[idx:])
                    self._seq.append(newInteger.value)
                else:
                    itemLen, itemIdx = self._decodeLen(idx + 1, self.payload)
                    self._seq.append(self.payload[idx:itemIdx + itemLen])
                    idx = itemIdx + itemLen
        except IndexError:
            raise ValueError("Not a valid DER SEQUENCE.")
        return tlvLength
