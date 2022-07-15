from mininet.topo import Topo


class Topology(Topo):
    def __init__(self):
        """ init topology """
        Topo.__init__(self)

        """ add the hosts & switches """
        h1 = self.addHost('h1')
        h3 = self.addHost('h3')
        h4 = self.addHost('h4')
        h2 = self.addHost('h2')
        s1 = self.addSwitch('s1', protocols='OpenFlow13')
        s2 = self.addSwitch('s2', protocols='OpenFlow13')
        s3 = self.addSwitch('s3', protocols='OpenFlow13')

        """ add links """
        self.addLink(h1, s1)
        self.addLink(h2, s2)
        self.addLink(h4, s3)
        self.addLink(h3, s3)
        self.addLink(s1, s2)
        self.addLink(s2, s3)


topos = {'topo': (lambda: Topology())}
