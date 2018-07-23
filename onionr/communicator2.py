#!/usr/bin/env python3
'''
    Onionr - P2P Microblogging Platform & Social network.

    This file contains both the OnionrCommunicate class for communcating with peers
    and code to operate as a daemon, getting commands from the command queue database (see core.Core.daemonQueue)
'''
'''
    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <https://www.gnu.org/licenses/>.
'''
import sys, os, core, config, json, onionrblockapi as block, requests, time, logger, threading, onionrplugins as plugins, base64, onionr
import onionrexceptions
from defusedxml import minidom

class OnionrCommunicatorDaemon:
    def __init__(self, debug, developmentMode):

        # list of timer instances
        self.timers = []

        # initalize core with Tor socks port being 3rd argument
        self.proxyPort = sys.argv[2]
        self._core = core.Core(torPort=self.proxyPort)

        # intalize NIST beacon salt and time
        self.nistSaltTimestamp = 0
        self.powSalt = 0
        
        self.blockToUpload = ''

        # loop time.sleep delay in seconds
        self.delay = 1

        # time app started running for info/statistics purposes
        self.startTime = self._core._utils.getEpoch()

        # lists of connected peers and peers we know we can't reach currently
        self.onlinePeers = []
        self.offlinePeers = []

        # amount of threads running by name, used to prevent too many
        self.threadCounts = {}

        # set true when shutdown command recieved
        self.shutdown = False

        # list of new blocks to download, added to when new block lists are fetched from peers
        self.blockQueue = []

        # list of blocks currently downloading, avoid s
        self.currentDownloading = []

        # Clear the daemon queue for any dead messages
        if os.path.exists(self._core.queueDB):
            self._core.clearDaemonQueue()

        # Loads in and starts the enabled plugins
        plugins.reload()

        if debug or developmentMode:
            OnionrCommunicatorTimers(self, self.heartbeat, 10)

        # Print nice header thing :)
        if config.get('general.display_header', True) and not self.shutdown:
            self.header()

        # Set timers, function reference, seconds
        OnionrCommunicatorTimers(self, self.daemonCommands, 5)
        OnionrCommunicatorTimers(self, self.detectAPICrash, 5)
        peerPoolTimer = OnionrCommunicatorTimers(self, self.getOnlinePeers, 60)
        OnionrCommunicatorTimers(self, self.lookupBlocks, 7, requiresPeer=True)
        OnionrCommunicatorTimers(self, self.getBlocks, 10, requiresPeer=True)
        OnionrCommunicatorTimers(self, self.clearOfflinePeer, 58)
        OnionrCommunicatorTimers(self, self.lookupKeys, 60, requiresPeer=True)
        OnionrCommunicatorTimers(self, self.lookupAdders, 60, requiresPeer=True)

        # set loop to execute instantly to load up peer pool (replaced old pool init wait)
        peerPoolTimer.count = (peerPoolTimer.frequency - 1)

        # Main daemon loop, mainly for calling timers, don't do any complex operations here to avoid locking
        try:
            while not self.shutdown:
                for i in self.timers:
                    if self.shutdown:
                        break
                    i.processTimer()
                time.sleep(self.delay)
        except KeyboardInterrupt:
            self.shutdown = True
            pass

        logger.info('Goodbye.')
        self._core._utils.localCommand('shutdown')
        time.sleep(0.5)

    def lookupKeys(self):
        '''Lookup new keys'''
        logger.debug('Looking up new keys...')
        tryAmount = 1
        for i in range(tryAmount):
            # Download new key list from random online peers
            peer = self.pickOnlinePeer()
            newKeys = self.peerAction(peer, action='kex')
            self._core._utils.mergeKeys(newKeys)
        self.decrementThreadCount('lookupKeys')
        return

    def lookupAdders(self):
        '''Lookup new peer addresses'''
        logger.info('LOOKING UP NEW ADDRESSES')
        tryAmount = 1
        for i in range(tryAmount):
            # Download new peer address list from random online peers
            peer = self.pickOnlinePeer()
            newAdders = self.peerAction(peer, action='pex')
            self._core._utils.mergeAdders(newAdders)
        self.decrementThreadCount('lookupAdders')

    def lookupBlocks(self):
        '''Lookup new blocks & add them to download queue'''
        logger.info('LOOKING UP NEW BLOCKS')
        tryAmount = 2
        newBlocks = ''
        existingBlocks = self._core.getBlockList()
        for i in range(tryAmount):
            peer = self.pickOnlinePeer() # select random online peer
            newDBHash = self.peerAction(peer, 'getDBHash') # get their db hash
            if newDBHash == False:
                continue # if request failed, restart loop (peer is added to offline peers automatically)
            if newDBHash != self._core.getAddressInfo(peer, 'DBHash'):
                self._core.setAddressInfo(peer, 'DBHash', newDBHash)
                newBlocks = self.peerAction(peer, 'getBlockHashes')
                if newBlocks != False:
                    # if request was a success
                    for i in newBlocks.split('\n'):
                        if self._core._utils.validateHash(i):
                            # if newline seperated string is valid hash
                            if not i in existingBlocks:
                                # if block does not exist on disk and is not already in block queue
                                if i not in self.blockQueue:
                                    self.blockQueue.append(i)
        self.decrementThreadCount('lookupBlocks')
        return

    def getBlocks(self):
        '''download new blocks in queue'''
        for blockHash in self.blockQueue:
            if blockHash in self.currentDownloading:
                logger.debug('ALREADY DOWNLOADING ' + blockHash)
                continue
            self.currentDownloading.append(blockHash)
            logger.info("Attempting to download %s..." % blockHash)
            content = self.peerAction(self.pickOnlinePeer(), 'getData', data=blockHash) # block content from random peer (includes metadata)
            if content != False:
                try:
                    content = content.encode()
                except AttributeError:
                    pass
                content = base64.b64decode(content) # content is base64 encoded in transport
                realHash = self._core._crypto.sha3Hash(content)
                try:
                    realHash = realHash.decode() # bytes on some versions for some reason
                except AttributeError:
                    pass
                if realHash == blockHash:
                    content = content.decode() # decode here because sha3Hash needs bytes above
                    metas = self._core._utils.getBlockMetadataFromData(content) # returns tuple(metadata, meta), meta is also in metadata
                    metadata = metas[0]
                    #meta = metas[1]
                    if self._core._utils.validateMetadata(metadata): # check if metadata is valid
                        if self._core._crypto.verifyPow(content): # check if POW is enough/correct
                            logger.info('Block passed proof, saving.')
                            self._core.setData(content)
                            self._core.addToBlockDB(blockHash, dataSaved=True)
                            self._core._utils.processBlockMetadata(blockHash) # caches block metadata values to block database
                        else:
                            logger.warn('POW failed for block ' + blockHash)
                    else:
                        logger.warn('Metadata for ' + blockHash + ' is invalid.')
                else:
                    # if block didn't meet expected hash
                    tempHash = self._core._crypto.sha3Hash(content) # lazy hack, TODO use var
                    try:
                        tempHash = tempHash.decode()
                    except AttributeError:
                        pass
                    logger.warn('Block hash validation failed for ' + blockHash + ' got ' + tempHash)
                self.blockQueue.remove(blockHash) # remove from block queue both if success or false
            self.currentDownloading.remove(blockHash)
        self.decrementThreadCount('getBlocks')
        return

    def pickOnlinePeer(self):
        '''randomly picks peer from pool without bias (using secrets module)'''
        retData = ''
        while True:
            peerLength = len(self.onlinePeers)
            if peerLength <= 0:
                break
            try:
                # get a random online peer, securely. May get stuck in loop if network is lost or if all peers in pool magically disconnect at once
                retData = self.onlinePeers[self._core._crypto.secrets.randbelow(peerLength)]
            except IndexError:
                pass
            else:
                break
        return retData

    def decrementThreadCount(self, threadName):
        '''Decrement amount of a thread name if more than zero, called when a function meant to be run in a thread ends'''
        try:
            if self.threadCounts[threadName] > 0:
                self.threadCounts[threadName] -= 1
        except KeyError:
            pass

    def clearOfflinePeer(self):
        '''Removes the longest offline peer to retry later'''
        try:
            removed = self.offlinePeers.pop(0)
        except IndexError:
            pass
        else:
            logger.debug('Removed ' + removed + ' from offline list, will try them again.')
        self.decrementThreadCount('clearOfflinePeer')

    def getOnlinePeers(self):
        '''Manages the self.onlinePeers attribute list, connects to more peers if we have none connected'''

        logger.info('Refreshing peer pool.')
        maxPeers = 4
        needed = maxPeers - len(self.onlinePeers)

        for i in range(needed):
            if len(self.onlinePeers) == 0:
                self.connectNewPeer(useBootstrap=True)
            if self.shutdown:
                break
        else:
            if len(self.onlinePeers) == 0:
                logger.warn('Could not connect to any peer.')
        self.decrementThreadCount('getOnlinePeers')

    def addBootstrapListToPeerList(self, peerList):
        '''Add the bootstrap list to the peer list (no duplicates)'''
        for i in self._core.bootstrapList:
            if i not in peerList and i not in self.offlinePeers and i != self._core.hsAdder:
                peerList.append(i)

    def connectNewPeer(self, peer='', useBootstrap=False):
        '''Adds a new random online peer to self.onlinePeers'''
        retData = False
        tried = self.offlinePeers
        if peer != '':
            if self._core._utils.validateID(peer):
                peerList = [peer]
            else:
                raise onionrexceptions.InvalidAddress('Will not attempt connection test to invalid address')
        else:
            peerList = self._core.listAdders()

        if len(peerList) == 0 or useBootstrap:
            # Avoid duplicating bootstrap addresses in peerList
            self.addBootstrapListToPeerList(peerList)

        for address in peerList:
            if len(address) == 0 or address in tried or address in self.onlinePeers:
                continue
            if self.peerAction(address, 'ping') == 'pong!':
                logger.info('Connected to ' + address)
                time.sleep(0.1)
                if address not in self.onlinePeers:
                    self.onlinePeers.append(address)
                retData = address
                break
            else:
                tried.append(address)
                logger.debug('Failed to connect to ' + address)
        return retData

    def printOnlinePeers(self):
        '''logs online peer list'''
        if len(self.onlinePeers) == 0:
            logger.warn('No online peers')
        else:
            logger.info('Online peers:')
            for i in self.onlinePeers:
                logger.info(i)

    def peerAction(self, peer, action, data=''):
        '''Perform a get request to a peer'''
        if len(peer) == 0:
            return False
        logger.info('Performing ' + action + ' with ' + peer + ' on port ' + str(self.proxyPort))
        url = 'http://' + peer + '/public/?action=' + action
        if len(data) > 0:
            url += '&data=' + data
        retData = self._core._utils.doGetRequest(url, port=self.proxyPort)
        # if request failed, (error), mark peer offline
        if retData == False:
            try:
                self.onlinePeers.remove(peer)
                self.getOnlinePeers() # Will only add a new peer to pool if needed
            except ValueError:
                pass
        return retData

    def heartbeat(self):
        '''Show a heartbeat debug message'''
        currentTime = self._core._utils.getEpoch() - self.startTime
        logger.debug('heartbeat, running seconds: ' + str(currentTime))
        self.decrementThreadCount('heartbeat')

    def daemonCommands(self):
        '''process daemon commands from daemonQueue'''
        cmd = self._core.daemonQueue()

        if cmd is not False:
            if cmd[0] == 'shutdown':
                self.shutdown = True
            elif cmd[0] == 'announceNode':
                self.announce(cmd[1])
            elif cmd[0] == 'runCheck':
                logger.debug('Status check; looks good.')
                open('data/.runcheck', 'w+').close()
            elif cmd[0] == 'connectedPeers':
                self.printOnlinePeers()
            elif cmd[0] == 'kex':
                for i in self.timers:
                    if i.timerFunction.__name__ == 'lookupKeys':
                        i.count = (i.frequency - 1)
            elif cmd[0] == 'uploadBlock':
                self.blockToUpload = cmd[1]
                threading.Thread(target=self.uploadBlock).start()
            else:
                logger.info('Recieved daemonQueue command:' + cmd[0])
        self.decrementThreadCount('daemonCommands')

    def uploadBlock(self):
        triedPeers = []
        if not self._core._utils.validateHash(self.blockToUpload):
            logger.warn('Requested to upload invalid block')
            return
        for i in range(max(len(self.onlinePeers), 2)):
            peer = self.pickOnlinePeer()
            if peer in triedPeers:
                continue
            triedPeers.append(peer)
            url = 'http://' + peer + '/public/upload/'
            data = {'block': block.Block(self.blockToUpload).getRaw()}
            if peer.endswith('.onion'):
                proxyType = 'tor'
            elif peer.endswith('.i2p'):
                proxyType = 'i2p'
            logger.info("Uploading block")
            self._core._utils.doPostRequest(url, data=data, proxyType=proxyType)

    def announce(self, peer):
        '''Announce to peers our address'''
        announceCount = 0
        announceAmount = 2
        for peer in self.onlinePeers:
            announceCount += 1
            if self.peerAction(peer, 'announce', self._core.hsAdder) == 'Success':
                logger.info('Successfully introduced node to ' + peer)
                break
            else:
                if announceCount == announceAmount:
                    logger.warn('Could not introduce node. Try again soon')
                    break

    def detectAPICrash(self):
        '''exit if the api server crashes/stops'''
        if self._core._utils.localCommand('ping', silent=False) != 'pong':
            for i in range(5):
                if self._core._utils.localCommand('ping') == 'pong':
                    break # break for loop
                time.sleep(1)
            else:
                # This executes if the api is NOT detected to be running
                logger.error('Daemon detected API crash (or otherwise unable to reach API after long time), stopping...')
                self.shutdown = True
        self.decrementThreadCount('detectAPICrash')

    def header(self, message = logger.colors.fg.pink + logger.colors.bold + 'Onionr' + logger.colors.reset + logger.colors.fg.pink + ' has started.'):
        if os.path.exists('static-data/header.txt'):
            with open('static-data/header.txt', 'rb') as file:
                # only to stdout, not file or log or anything
                sys.stderr.write(file.read().decode().replace('P', logger.colors.fg.pink).replace('W', logger.colors.reset + logger.colors.bold).replace('G', logger.colors.fg.green).replace('\n', logger.colors.reset + '\n').replace('B', logger.colors.bold).replace('V', onionr.ONIONR_VERSION))
                logger.info(logger.colors.fg.lightgreen + '-> ' + str(message) + logger.colors.reset + logger.colors.fg.lightgreen + ' <-\n')

class OnionrCommunicatorTimers:
    def __init__(self, daemonInstance, timerFunction, frequency, makeThread=True, threadAmount=1, maxThreads=5, requiresPeer=False):
        self.timerFunction = timerFunction
        self.frequency = frequency
        self.threadAmount = threadAmount
        self.makeThread = makeThread
        self.requiresPeer = requiresPeer
        self.daemonInstance = daemonInstance
        self.maxThreads = maxThreads
        self._core = self.daemonInstance._core

        self.daemonInstance.timers.append(self)
        self.count = 0

    def processTimer(self):

        # mark how many instances of a thread we have (decremented at thread end)
        try:
            self.daemonInstance.threadCounts[self.timerFunction.__name__]
        except KeyError:
            self.daemonInstance.threadCounts[self.timerFunction.__name__] = 0

        # execute thread if it is time, and we are not missing *required* online peer
        if self.count == self.frequency:
            try:
                if self.requiresPeer and len(self.daemonInstance.onlinePeers) == 0:
                    raise onionrexceptions.OnlinePeerNeeded
            except onionrexceptions.OnlinePeerNeeded:
                pass
            else:
                if self.makeThread:
                    for i in range(self.threadAmount):
                        if self.daemonInstance.threadCounts[self.timerFunction.__name__] >= self.maxThreads:
                            logger.warn(self.timerFunction.__name__ + ' has too many current threads to start anymore.')
                        else:
                            self.daemonInstance.threadCounts[self.timerFunction.__name__] += 1
                            newThread = threading.Thread(target=self.timerFunction)
                            newThread.start()
                else:
                    self.timerFunction()
            self.count = -1 # negative 1 because its incremented at bottom
        self.count += 1

shouldRun = False
debug = True
developmentMode = False
if config.get('general.dev_mode', True):
    developmentMode = True
try:
    if sys.argv[1] == 'run':
        shouldRun = True
except IndexError:
    pass
if shouldRun:
    try:
        OnionrCommunicatorDaemon(debug, developmentMode)
    except Exception as e:
        logger.error('Error occured in Communicator', error = e, timestamp = False)
