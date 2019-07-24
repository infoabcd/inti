import json
from onionrutils import bytesconverter, epoch
import storagecounter, filepaths, onionrstorage
import onionrevents as events
from etc import powchoice, onionrvalues
import config, onionrcrypto as crypto, subprocesspow, onionrexceptions
from onionrusers import onionrusers
from onionrutils import localcommand, blockmetadata, stringvalidators
import coredb
def insert_block(data, header='txt', sign=False, encryptType='', symKey='', asymPeer='', meta = {}, expire=None, disableForward=False):
    '''
        Inserts a block into the network
        encryptType must be specified to encrypt a block
    '''
    use_subprocess = powchoice.use_subprocess(config)
    requirements = onionrvalues.OnionrValues()
    storage_counter = storagecounter.StorageCounter()
    allocationReachedMessage = 'Cannot insert block, disk allocation reached.'
    if storage_counter.isFull():
        logger.error(allocationReachedMessage)
        return False
    retData = False

    if type(data) is None:
        raise ValueError('Data cannot be none')

    createTime = epoch.get_epoch()

    dataNonce = bytesconverter.bytes_to_str(crypto.hashers.sha3_hash(data))
    try:
        with open(filepaths.data_nonce_file, 'r') as nonces:
            if dataNonce in nonces:
                return retData
    except FileNotFoundError:
        pass
    # record nonce
    with open(filepaths.data_nonce_file, 'a') as nonceFile:
        nonceFile.write(dataNonce + '\n')

    if type(data) is bytes:
        data = data.decode()
    data = str(data)
    plaintext = data
    plaintextMeta = {}
    plaintextPeer = asymPeer

    retData = ''
    signature = ''
    signer = ''
    metadata = {}
    # metadata is full block metadata, meta is internal, user specified metadata

    # only use header if not set in provided meta

    meta['type'] = str(header)

    if encryptType in ('asym', 'sym', ''):
        metadata['encryptType'] = encryptType
    else:
        raise onionrexceptions.InvalidMetadata('encryptType must be asym or sym, or blank')

    try:
        data = data.encode()
    except AttributeError:
        pass

    if encryptType == 'asym':
        meta['rply'] = createTime # Duplicate the time in encrypted messages to prevent replays
        if not disableForward and sign and asymPeer != crypto.pub_key:
            try:
                forwardEncrypted = onionrusers.OnionrUser(asymPeer).forwardEncrypt(data)
                data = forwardEncrypted[0]
                meta['forwardEnc'] = True
                expire = forwardEncrypted[2] # Expire time of key. no sense keeping block after that
            except onionrexceptions.InvalidPubkey:
                pass
                #onionrusers.OnionrUser(self, asymPeer).generateForwardKey()
            fsKey = onionrusers.OnionrUser(asymPeer).generateForwardKey()
            #fsKey = onionrusers.OnionrUser(self, asymPeer).getGeneratedForwardKeys().reverse()
            meta['newFSKey'] = fsKey
    jsonMeta = json.dumps(meta)
    plaintextMeta = jsonMeta
    if sign:
        signature = crypto.signing.ed_sign(jsonMeta.encode() + data, key=crypto.priv_key, encodeResult=True)
        signer = crypto.pub_key

    if len(jsonMeta) > 1000:
        raise onionrexceptions.InvalidMetadata('meta in json encoded form must not exceed 1000 bytes')

    # encrypt block metadata/sig/content
    if encryptType == 'sym':
        raise NotImplementedError("not yet implemented")
    elif encryptType == 'asym':
        if stringvalidators.validate_pub_key(asymPeer):
            # Encrypt block data with forward secrecy key first, but not meta
            jsonMeta = json.dumps(meta)
            jsonMeta = crypto.encryption.pub_key_encrypt(jsonMeta, asymPeer, encodedData=True).decode()
            data = crypto.encryption.pub_key_encrypt(data, asymPeer, encodedData=True).decode()
            signature = crypto.encryption.pub_key_encrypt(signature, asymPeer, encodedData=True).decode()
            signer = crypto.encryption.pub_key_encrypt(signer, asymPeer, encodedData=True).decode()
            try:
                onionrusers.OnionrUser(asymPeer, saveUser=True)
            except ValueError:
                # if peer is already known
                pass
        else:
            raise onionrexceptions.InvalidPubkey(asymPeer + ' is not a valid base32 encoded ed25519 key')

    # compile metadata
    metadata['meta'] = jsonMeta
    metadata['sig'] = signature
    metadata['signer'] = signer
    metadata['time'] = createTime

    # ensure expire is integer and of sane length
    if type(expire) is not type(None):
        assert len(str(int(expire))) < 14
        metadata['expire'] = expire

    # send block data (and metadata) to POW module to get tokenized block data
    if use_subprocess:
        payload = subprocesspow.SubprocessPOW(data, metadata).start()
    else:
        payload = onionrproofs.POW(metadata, data).waitForResult()
    if payload != False:
        try:
            retData = onionrstorage.set_data(payload)
        except onionrexceptions.DiskAllocationReached:
            logger.error(allocationReachedMessage)
            retData = False
        else:
            # Tell the api server through localCommand to wait for the daemon to upload this block to make statistical analysis more difficult
            if localcommand.local_command('/ping', maxWait=10) == 'pong!':
                if config.get('general.security_level', 1) == 0:
                    localcommand.local_command('/waitforshare/' + retData, post=True, maxWait=5)
                coredb.daemonqueue.daemon_queue_add('uploadBlock', retData)
            else:
                pass
            coredb.blockmetadb.add.add_to_block_DB(retData, selfInsert=True, dataSaved=True)
            blockmetadata.process_block_metadata(retData)
    '''
    if retData != False:
        if plaintextPeer == onionrvalues.DENIABLE_PEER_ADDRESS:
            events.event('insertdeniable', {'content': plaintext, 'meta': plaintextMeta, 'hash': retData, 'peer': bytesconverter.bytes_to_str(asymPeer)}, onionr = self.onionrInst, threaded = True)
        else:
            events.event('insertblock', {'content': plaintext, 'meta': plaintextMeta, 'hash': retData, 'peer': bytesconverter.bytes_to_str(asymPeer)}, onionr = self.onionrInst, threaded = True)
    '''
    return retData