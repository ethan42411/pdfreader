import logging
from base64 import b85encode
from copy import deepcopy

from ..codecs.decoder import Decoder, default_decoder
from ..parsers.content import ContentParser
from ..types.content import Operator, InlineImage
from ..types.native import HexString, String, Dictionary, Array, Boolean, Name, Decimal, Integer
from ..types.objects import Image, Form
from ..utils import pdf_escape_string
from .canvas import SimpleCanvas
from .resources import Resources
from .pdfviewer import PDFViewer, ContextualViewer


def object_to_string(obj):
    if obj is None:
        val = "null"
    elif isinstance(obj, Boolean):
        val = str(obj).lower()
    elif isinstance(obj, Name):
        val = "/" + obj
    elif isinstance(obj, str):
        val = obj
    elif isinstance(obj, (int, Integer, Decimal)):
        val = str(obj)
    elif isinstance(obj, Array):
        val = "[" + " ".join([object_to_string(elm) for elm in obj]) + "]"
    elif isinstance(obj, Dictionary):
        val = "<<" + " ".join(["/{} {}".format(k, object_to_string(v)) for k, v in obj.items()]) + ">>"
    elif isinstance(obj, Operator):
        operands = " ".join([object_to_string(a) for a in obj.args])
        val = "\n{} {}".format(operands, obj.name)
    elif isinstance(obj, InlineImage):
        # Convert bytes to string representation
        # We encode the image with ASCII85 to make it a unicode string
        entries = " ".join(["/{} {}".format(k, object_to_string(v))
                            for k, v in obj.dictionary.items()
                            if k not in ('F', 'Filter', 'DecodeParms')])
        entries += " /Filter /ASCII85Decode"
        content = b85encode(obj.filtered) + b'~>'
        val = "\nBI\n{entries}\nID\n{content}\nEI".format(entries=entries, content=content.decode('ascii'))
    else:
        raise ValueError("Unexpected object: {}. Possibly a bug.".format(obj))
    return val


class TextOperatorsMixin(object):

    parser_class = ContentParser
    canvas_class = SimpleCanvas
    operators_aliases = {"'": "apostrophe",
                         '"': "quotation",
                         'T*': "Tstar"}

    def __init__(self, *args, **kwargs):
        super(TextOperatorsMixin, self).__init__(*args, **kwargs)
        self.bracket_commands_stack = [] # one day we may start support BX/EX, MDC/BMC/EMC.
                                         # BI/EI comes as a part of ContentParser due to inline image object nature
        self._decoders = dict()

    @property
    def mode(self):
        """ Current interpreter mode reflects the most recent command brackets """
        if self.bracket_commands_stack:
            return self.bracket_commands_stack[-1].name

    @property
    def decoder(self):
        name = self.gss.state.font_name
        if name not in self._decoders:
            if name in self.resources.Font:
                obj = Decoder(self.resources.Font[name])
            else:
                obj = default_decoder
            self._decoders[name] = obj
        return self._decoders[name]

    def decode_string(self, s):
        if isinstance(s, HexString):
            s = self.decoder.decode_hexstring(s)
        else:
            s = self.decoder.decode_string(s)
        return s

    def after_handler(self, obj):
        """ Put object on canvas """
        self.canvas.text_content += object_to_string(obj)

    def on_inline_image(self, obj):
        self.canvas.inline_images.append(obj)


    def on_BT(self, op):
        if self.mode == "BT":
            raise ValueError("BT operator without enclosing ET")
        self.bracket_commands_stack.append(op)

    def on_ET(self, op):
        if self.mode != "BT":
            raise ValueError("ET operator without corresponding BT")
        self.bracket_commands_stack.pop()

    # Text handlers
    def on_Tf(self, op):
        """ Set font name and size """
        self.gss.state.Font = op.args

    def on_Tj(self, op):
        """ Show text string
            Decode it, add on canvas.strings and replace operator's argument
        """
        s = self.decode_string(op.args[0])
        self.canvas.strings.append(s)
        op.args = ["({})".format(pdf_escape_string(s))]

    on_apostrophe = on_Tj

    def on_TJ(self, op):
        """ Show one or more text strings  """
        arr = op.args[0]
        for i in range(len(arr)):
            if isinstance(arr[i], (HexString, String)):
                s = self.decode_string(arr[i])
                self.canvas.strings.append(s)
                arr[i] = "({})".format(pdf_escape_string(s))

    on_quotation = on_TJ

    def on_Tstar(self, op):
        """ Moves start to the next line.
            Does it make sence to add "\n" to canvas.strings ???
            Do nothing until figure out
        """
        pass


class SimplePDFViewer(TextOperatorsMixin, PDFViewer):

    def on_Do(self, op):
        name = op.args[0]
        xobj = self.resources.XObject.get(name)
        if not xobj:
            logging.warning("Can't locate XObject {}".format(name))
        else:
            if isinstance(xobj, Image) and name not in self.canvas.forms:
                self.canvas.images[name] = xobj
            elif isinstance(xobj, Form) and name not in self.canvas.forms:
                # render form and save
                resources = Resources.from_page(self.current_page,
                                                resources_stack=[xobj.Resources])
                subviewer = FormViewer(xobj.filtered, resources, self.gss)
                subviewer.render()
                self.canvas.forms[name] = subviewer.canvas


class FormViewer(TextOperatorsMixin, ContextualViewer):
    """ Forms sub-viewer  """

    pass