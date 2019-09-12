'''
    Onionr - Private P2P Communication

    Upload blocks in the upload queue to peers from the communicator
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
import logger
from communicatorutils import proxypicker
import onionrexceptions
import onionrblockapi as block
from onionrutils import localcommand, stringvalidators, basicrequests
from communicator import onlinepeers
import onionrcrypto
def upload_blocks_from_communicator(comm_inst):
    # when inserting a block, we try to upload it to a few peers to add some deniability
    TIMER_NAME = "upload_blocks_from_communicator"

    triedPeers = []
    finishedUploads = []
    comm_inst.blocksToUpload = onionrcrypto.cryptoutils.random_shuffle(comm_inst.blocksToUpload)
    if len(comm_inst.blocksToUpload) != 0:
        for bl in comm_inst.blocksToUpload:
            if not stringvalidators.validate_hash(bl):
                logger.warn('Requested to upload invalid block', terminal=True)
                comm_inst.decrementThreadCount(TIMER_NAME)
                return
            for i in range(min(len(comm_inst.onlinePeers), 6)):
                peer = onlinepeers.pick_online_peer(comm_inst)
                if peer in triedPeers:
                    continue
                triedPeers.append(peer)
                url = 'http://' + peer + '/upload'
                try:
                    #data = {'block': block.Block(bl).getRaw()}
                    data = block.Block(bl).getRaw()
                except onionrexceptions.NoDataAvailable:
                    finishedUploads.append(bl)
                    break
                proxyType = proxypicker.pick_proxy(peer)
                logger.info("Uploading block %s to %s" % (bl[:8], peer), terminal=True)
                resp = basicrequests.do_post_request(url, data=data, proxyType=proxyType, content_type='application/octet-stream')
                if not resp == False:
                    if resp == 'success':
                        localcommand.local_command('waitforshare/' + bl, post=True)
                        finishedUploads.append(bl)
                    elif resp == 'exists':
                        finishedUploads.append(bl)
                    else:
                        logger.warn('Failed to upload %s, reason: %s' % (bl[:8], resp[:150]), terminal=True)
    for x in finishedUploads:
        try:
            comm_inst.blocksToUpload.remove(x)
        except ValueError:
            pass
    comm_inst.decrementThreadCount(TIMER_NAME)
