"""Exact ASCII-SOH codec for the implemented simulated FIX 4.4 subset."""
from __future__ import annotations
import hashlib
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from secure_eval_wrapper.fix.models import FixMessage, FixMessageType, FixOrderType, FixSide, FixTimeInForce, FixOrdStatus, FixExecType
from secure_eval_wrapper.fix import tags
SOH=b"\x01"

class FixValidationError(ValueError): pass

_REQUIRED={
 FixMessageType.LOGON:{98,108}, FixMessageType.HEARTBEAT:set(), FixMessageType.TEST_REQUEST:{112}, FixMessageType.RESEND_REQUEST:{7,16}, FixMessageType.SEQUENCE_RESET:{36}, FixMessageType.LOGOUT:set(), FixMessageType.REJECT:{45},
 FixMessageType.NEW_ORDER_SINGLE:{11,55,54,60,38,40,59}, FixMessageType.ORDER_CANCEL_REQUEST:{11,41,55,54,60,38}, FixMessageType.EXECUTION_REPORT:{37,17,11,55,54,39,150,151,14,6}, FixMessageType.ORDER_CANCEL_REJECT:{37,11,41,39,434}, FixMessageType.BUSINESS_MESSAGE_REJECT:{372,380},
}
_ORDER={98:10,108:20,141:30,112:40,7:50,16:60,36:70,123:80,45:90,371:95,372:100,373:110,380:120,102:130,434:140,11:200,41:210,37:220,17:230,55:240,54:250,60:260,38:270,40:280,44:290,99:300,59:310,39:320,150:330,151:340,14:350,6:360,58:370}
_HEADER={8,9,35,34,49,56,52,43,122,10}; _KNOWN=_HEADER|set(_ORDER)
_DECIMAL_TAGS={38,44,99,151,14,6}; _INTEGER_TAGS={34,7,16,36,45,98,108,102,373,380,434}; _TIME_TAGS={52,60,122}

def format_utc(value:datetime)->str:
 if value.tzinfo is None or value.utcoffset() is None: raise FixValidationError("FIX timestamp must be timezone-aware UTC")
 value=value.astimezone(timezone.utc); base=value.strftime("%Y%m%d-%H:%M:%S"); return base if value.microsecond==0 else f"{base}.{value.microsecond:06d}".rstrip("0")

def parse_utc(value:str)->datetime:
 try:
  parsed=datetime.strptime(value,"%Y%m%d-%H:%M:%S.%f" if "." in value else "%Y%m%d-%H:%M:%S").replace(tzinfo=timezone.utc)
 except ValueError as exc: raise FixValidationError("invalid FIX UTC timestamp") from exc
 return parsed

def _validate_fields(msg_type,fields):
 missing=sorted(_REQUIRED[msg_type]-set(fields))
 if missing: raise FixValidationError(f"missing required FIX tags: {missing}")
 for tag in _INTEGER_TAGS & set(fields):
  try: value=int(fields[tag])
  except ValueError as exc: raise FixValidationError(f"invalid integer in tag {tag}") from exc
  if value<0: raise FixValidationError(f"negative integer in tag {tag}")
 for tag in _DECIMAL_TAGS & set(fields):
  try: value=Decimal(fields[tag])
  except InvalidOperation as exc: raise FixValidationError(f"invalid Decimal in tag {tag}") from exc
  if not value.is_finite(): raise FixValidationError(f"non-finite Decimal in tag {tag}")
 for tag in _TIME_TAGS & set(fields): parse_utc(fields[tag])
 if 54 in fields:
  try: FixSide(fields[54])
  except ValueError as exc: raise FixValidationError("invalid Side") from exc
 if 40 in fields:
  try: kind=FixOrderType(fields[40])
  except ValueError as exc: raise FixValidationError("unsupported OrdType") from exc
  if kind is FixOrderType.LIMIT and 44 not in fields: raise FixValidationError("limit order requires Price")
  if kind is FixOrderType.STOP and 99 not in fields: raise FixValidationError("stop order requires StopPx")
  if kind is FixOrderType.STOP_LIMIT and (44 not in fields or 99 not in fields): raise FixValidationError("stop-limit order requires Price and StopPx")
 if 59 in fields:
  try: FixTimeInForce(fields[59])
  except ValueError as exc: raise FixValidationError("unsupported TimeInForce") from exc
 if 39 in fields:
  try: FixOrdStatus(fields[39])
  except ValueError as exc: raise FixValidationError("invalid OrdStatus") from exc
 if 150 in fields:
  try: FixExecType(fields[150])
  except ValueError as exc: raise FixValidationError("invalid ExecType") from exc
 if 434 in fields and fields[434] != "1": raise FixValidationError("unsupported CxlRejResponseTo")
 if 380 in fields and fields[380] not in {"0","1","2","3","4","5","6","7"}: raise FixValidationError("unsupported BusinessRejectReason")

class FixCodec:
 def __init__(self,*,preserve_unknown_tags:bool=False): self.preserve_unknown_tags=preserve_unknown_tags
 def encode(self,message:FixMessage)->bytes:
  if message.extensions and not self.preserve_unknown_tags: raise FixValidationError("unknown FIX tags are disabled")
  fields=dict(message.fields); _validate_fields(message.msg_type,fields)
  header=[(35,message.msg_type.value),(34,str(message.msg_seq_num)),(49,message.sender_comp_id),(56,message.target_comp_id),(52,format_utc(message.sending_time_utc))]
  if message.poss_dup_flag: header.extend([(43,"Y"),(122,format_utc(message.orig_sending_time_utc))])
  body_tags=sorted(fields.items(),key=lambda item:(_ORDER.get(item[0],10000+item[0]),item[0]))+sorted(message.extensions.items())
  body=SOH.join(f"{tag}={value}".encode("ascii") for tag,value in header+body_tags)+SOH
  prefix=f"8=FIX.4.4\x019={len(body)}\x01".encode("ascii"); before_checksum=prefix+body; checksum=sum(before_checksum)%256
  return before_checksum+f"10={checksum:03d}\x01".encode("ascii")
 def decode(self,raw:bytes)->FixMessage:
  if not isinstance(raw,(bytes,bytearray)) or not raw or not bytes(raw).endswith(SOH): raise FixValidationError("FIX message must be non-empty bytes ending in SOH")
  raw=bytes(raw)
  try: text=raw.decode("ascii")
  except UnicodeDecodeError as exc: raise FixValidationError("FIX message must be ASCII") from exc
  pairs=[]
  for item in text[:-1].split("\x01"):
   if "=" not in item: raise FixValidationError("malformed FIX field")
   key,value=item.split("=",1)
   try: tag=int(key)
   except ValueError as exc: raise FixValidationError("FIX tag must be an integer") from exc
   if not value: raise FixValidationError(f"tag {tag} cannot be empty")
   pairs.append((tag,value))
  seen=set()
  for tag,_ in pairs:
   if tag in seen: raise FixValidationError(f"duplicate singleton tag {tag}")
   seen.add(tag)
  values=dict(pairs)
  if [tag for tag,_ in pairs[:2]]!=[8,9] or pairs[-1][0]!=10: raise FixValidationError("BeginString, BodyLength, and CheckSum ordering is invalid")
  if values[8]!="FIX.4.4": raise FixValidationError("unsupported BeginString")
  required_header={8,9,35,34,49,56,52,10}
  if required_header-set(values): raise FixValidationError(f"missing required header tags: {sorted(required_header-set(values))}")
  try: declared_length=int(values[9]); declared_checksum=int(values[10]); seq=int(values[34])
  except ValueError as exc: raise FixValidationError("invalid BodyLength, CheckSum, or MsgSeqNum") from exc
  if seq<=0 or declared_length<0 or declared_checksum<0 or declared_checksum>255: raise FixValidationError("invalid numeric header value")
  first_end=raw.find(SOH); second_end=raw.find(SOH,first_end+1); trailer_start=raw.rfind(b"10=")
  actual_length=trailer_start-(second_end+1)
  if actual_length!=declared_length: raise FixValidationError(f"incorrect BodyLength: expected {declared_length}, calculated {actual_length}")
  calculated=sum(raw[:trailer_start])%256
  if calculated!=declared_checksum: raise FixValidationError(f"incorrect CheckSum: expected {declared_checksum:03d}, calculated {calculated:03d}")
  try: msg_type=FixMessageType(values[35])
  except ValueError as exc: raise FixValidationError("unsupported MsgType") from exc
  unknown=set(values)-_KNOWN
  if unknown and not self.preserve_unknown_tags: raise FixValidationError(f"unknown FIX tags are disabled: {sorted(unknown)}")
  fields={tag:value for tag,value in pairs if tag not in _HEADER and tag in _KNOWN}; extensions={tag:values[tag] for tag in unknown}; _validate_fields(msg_type,{**fields,52:values[52]})
  poss=values.get(43,"N")
  if poss not in {"Y","N"}: raise FixValidationError("invalid PossDupFlag")
  orig=parse_utc(values[122]) if 122 in values else None
  if poss=="Y" and orig is None: raise FixValidationError("PossDupFlag requires OrigSendingTime")
  return FixMessage(msg_type=msg_type,msg_seq_num=seq,sender_comp_id=values[49],target_comp_id=values[56],sending_time_utc=parse_utc(values[52]),fields=fields,poss_dup_flag=poss=="Y",orig_sending_time_utc=orig,extensions=extensions,body_length=declared_length,checksum=declared_checksum,raw_message_sha256=hashlib.sha256(raw).hexdigest())
