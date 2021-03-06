# Taken from https://github.com/joeatwork/python-lzw/blob/master/lzw/__init__.py
# As the most recent version was not found on pypi
# - Removed six dependency as no py2 compatibility required.
# - Removed encoder part as we don't need it
# - added decode() for PDF streams support


import struct, logging

from .predictors import _remove_predictors

filter_names = ('LZWDecode', 'LZW')

CLEAR_CODE = 256
END_OF_INFO_CODE = 257

DEFAULT_MIN_BITS = 9
DEFAULT_MAX_BITS = 12


def decode(data, params):
    """
    >>> data = b'9\\x98M\\xa7\\x03a\\x94@t2\\x9e\\x0e\\x90\\x00'
    >>> decode(data, dict(Predictor=1))
    b'sample text'

    """
    try:
        data = decompress(data)
        data = _remove_predictors(data, params.get("Predictor"), params.get("Columns"))
    except ValueError:
        logging.exception("Skipping broken stream")
        data = b''
    return data


def decompress(compressed_bytes):
    """
    >>> decompress(b'9\\x98M\\xa7\\x03a\\x94@t2\\x9e\\x0e\\x90\\x00')
    b'sample text'

    """
    decoder = ByteDecoder()
    return decoder.decodefrombytes(compressed_bytes)


class ByteDecoder(object):
    """
    Decodes, combines bit-unpacking and interpreting a codepoint
    stream, suitable for use with bytes generated by
    L{ByteEncoder}.

    See L{ByteDecoder} for a usage example.
    """

    def __init__(self):
        """
        """

        self._decoder = Decoder()
        self._unpacker = BitUnpacker(initial_code_size=self._decoder.code_size())
        self.remaining = []

    def decodefrombytes(self, bytesource):
        """
        Given an iterator over BitPacked, Encoded bytes, Returns an
        iterator over the uncompressed bytes. Dual of
        L{ByteEncoder.encodetobytes}. See L{ByteEncoder} for an
        example of use.
        """
        codepoints = self._unpacker.unpack(bytesource)
        clearbytes = self._decoder.decode(codepoints)

        return clearbytes


class BitUnpacker(object):
    """
    An adaptive-width bit unpacker, intended to decode streams written
    by L{BitPacker} into integer codepoints. Like L{BitPacker}, knows
    about code size changes and control codes.
    """

    def __init__(self, initial_code_size):
        """
        initial_code_size is the starting size of the codebook
        associated with the to-be-unpacked stream.
        """
        self._initial_code_size = initial_code_size

    def unpack(self, bytesource):
        """
        Given an iterator of bytes, returns an iterator of integer
        code points. Auto-magically adjusts point width when it sees
        an almost-overflow in the input stream, or an LZW CLEAR_CODE
        or END_OF_INFO_CODE

        Trailing bits at the end of the given iterator, after the last
        codepoint, will be dropped on the floor.

        At the end of the iteration, or when an END_OF_INFO_CODE seen
        the unpacker will ignore the bits after the code until it
        reaches the next aligned byte. END_OF_INFO_CODE will *not*
        stop the generator, just reset the alignment and the width


        >>> unpk = BitUnpacker(initial_code_size=258)
        >>> [ i for i in unpk.unpack([0x00, 0xC0, 0x40]) ]
        [1, 257]
        """
        bits = []
        offset = 0
        ignore = 0

        codesize = self._initial_code_size
        minwidth = 8
        while (1 << minwidth) < codesize:
            minwidth = minwidth + 1

        pointwidth = minwidth

        for nextbit in bytestobits(bytesource):

            offset = (offset + 1) % 8
            if ignore > 0:
                ignore = ignore - 1
                continue

            bits.append(nextbit)

            if len(bits) == pointwidth:
                codepoint = intfrombits(bits)
                bits = []

                yield codepoint

                codesize = codesize + 1

                if codepoint in [CLEAR_CODE, END_OF_INFO_CODE]:
                    codesize = self._initial_code_size
                    pointwidth = minwidth
                else:
                    # is this too late?
                    while codesize >= (2 ** pointwidth):
                        pointwidth = pointwidth + 1

                if codepoint == END_OF_INFO_CODE:
                    ignore = (8 - offset) % 8


class Decoder(object):
    """
    Uncompresses a stream of lzw code points, as created by
    L{Encoder}. Given a list of integer code points, with all
    unpacking foolishness complete, turns that list of codepoints into
    a list of uncompressed bytes. See L{BitUnpacker} for what this
    doesn't do.
    """

    def __init__(self):
        """
        Creates a new Decoder. Decoders should not be reused for
        different streams.
        """
        self._clear_codes()
        self.remainder = []

    def code_size(self):
        """
        Returns the current size of the Decoder's code book, that is,
        it's mapping of codepoints to byte strings. The return value of
        this method will change as the decode encounters more encoded
        input, or control codes.
        """
        return len(self._codepoints)

    def decode(self, codepoints):
        """
        Given an iterable of integer codepoints, yields the
        corresponding bytes, one at a time, as byte strings of length
        E{1}. Retains the state of the codebook from call to call, so
        if you have another stream, you'll likely need another
        decoder!

        Decoders will NOT handle END_OF_INFO_CODE (rather, they will
        handle the code by throwing an exception); END_OF_INFO should
        be handled by the upstream codepoint generator (see
        L{BitUnpacker}, for example)

        >>> dec = Decoder()
        >>> dec.decode([103, 97, 98, 98, 97, 32, 258, 260, 262, 121, 111, 263, 259, 261, 256])
        b'gabba gabba yo gabba'

        """
        codepoints = [cp for cp in codepoints]

        decoded = b''
        for cp in codepoints:
            decoded += self._decode_codepoint(cp)
        return decoded

    def _decode_codepoint(self, codepoint):
        """
        Will raise a ValueError if given an END_OF_INFORMATION
        code. EOI codes should be handled by callers if they're
        present in our source stream.

        >>> dec = Decoder()
        >>> beforesize = dec.code_size()
        >>> dec._decode_codepoint(0x80) == b'\\x80'
        True
        >>> dec._decode_codepoint(0x81) == b'\\x81'
        True
        >>> beforesize + 1 == dec.code_size()
        True
        >>> dec._decode_codepoint(256) == b''
        True
        >>> beforesize == dec.code_size()
        True
        """

        ret = b""

        if codepoint == CLEAR_CODE:
            self._clear_codes()
        elif codepoint == END_OF_INFO_CODE:
            raise ValueError("End of information code not supported directly by this Decoder")
        else:
            if codepoint in self._codepoints:
                ret = self._codepoints[codepoint]
                if None != self._prefix:
                    self._codepoints[len(self._codepoints)] = self._prefix + ret[0:1]

            else:
                ret = self._prefix + ret[0:1]
                self._codepoints[len(self._codepoints)] = ret

            self._prefix = ret

        return ret

    def _clear_codes(self):
        self._codepoints = dict((pt, struct.pack("B", pt)) for pt in range(256))
        self._codepoints[CLEAR_CODE] = CLEAR_CODE
        self._codepoints[END_OF_INFO_CODE] = END_OF_INFO_CODE
        self._prefix = None


#########################################
# Conveniences.


def filebytes(fileobj, buffersize=1024):
    """
    Convenience for iterating over the bytes in a file. Given a
    file-like object (with a read(int) method), returns an iterator
    over the bytes of that file.
    """
    buff = fileobj.read(buffersize)
    while buff:
        for byte in buff: yield byte
        buff = fileobj.read(buffersize)


def intfrombits(bits):
    """
    Given a list of boolean values, interprets them as a binary
    encoded, MSB-first unsigned integer (with True == 1 and False
    == 0) and returns the result.

    >>> intfrombits([ 1, 0, 0, 1, 1, 0, 0, 0, 0 ])
    304
    """
    ret = 0
    lsb_first = [b for b in bits]
    lsb_first.reverse()

    for bit_index in range(len(lsb_first)):
        if lsb_first[bit_index]:
            ret = ret | (1 << bit_index)

    return ret


def bytestobits(bytesource):
    """
    Breaks a given iterable of bytes into an iterable of boolean
    values representing those bytes as unsigned integers.

    >>> [ x for x in bytestobits(b"\\x01\\x30") ]
    [0, 0, 0, 0, 0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 0, 0]
    """
    for value in bytesource:

        for bitplusone in range(8, 0, -1):
            bitindex = bitplusone - 1
            nextbit = 1 & (value >> bitindex)
            yield nextbit


if __name__ == "__main__":
    import doctest

    doctest.testmod()
