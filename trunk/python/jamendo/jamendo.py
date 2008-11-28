# -*- coding: utf-8 -*-
#
# Copyright (c) 2008 David JL <izimobil@gmail.com>
#
# Permission is hereby granted, free of charge, to any person obtaining a
# copy of this software and associated documentation files (the "Software"),
# to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense,
# and/or sell copies of the Software, and to permit persons to whom the
# Software is furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL 
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.

"""
Jamendo totem plugin (http://www.jamendo.com).

TODO:
- store thumbnails in relevant XDG directories (?)
- cleanup the notebook code
- interface with jamendo write API (not documented yet):
  favorites, comments, etc...
"""

import os
import totem
import gettext
import gconf
import gobject
import gtk
import gtk.glade
import pango
import socket
import threading
import time
import urllib
import urllib2
import webbrowser
from xml.sax.saxutils import escape
try:
    import json
except ImportError:
    try:
        import simplejson as json
    except ImportError:
        dlg = gtk.MessageDialog(
            type=gtk.MESSAGE_ERROR,
            buttons=gtk.BUTTONS_OK
        )
        dlg.set_markup(_('You need to install the python simplejson module'))
        dlg.run()
        dlg.destroy()
        raise

socket.setdefaulttimeout(30)
gobject.threads_init()
_ = gettext.gettext
gconf_key = '/apps/totem/plugins/jamendo'


class JamendoPlugin(totem.Plugin):
    """
    Jamendo totem plugin GUI.
    """
    SEARCH_CRITERIUM = ['artist_name', 'tag_idstr']
    AUDIO_FORMATS    = ['ogg2', 'mp31']
    TAB_RESULTS      = 0
    TAB_POPULAR      = 1
    TAB_LATEST       = 2

    def __init__(self):
        totem.Plugin.__init__(self)
        self.debug = True
        self.gstreamer_plugins_present = True
        self.totem = None
        self.gconf = gconf.client_get_default()
        self.init_settings()

        # init glade interface
        f = os.path.join(os.path.dirname(__file__), 'jamendo.glade')
        self.glade = gtk.glade.XML(f)
        self.config_dialog = self.glade.get_widget('config_dialog')
        self.window = self.glade.get_widget('mainwindow')
        self.container = self.glade.get_widget('mainwindow_container')
        self.notebook = self.glade.get_widget('notebook')
        self.search_entry = self.glade.get_widget('search_entry')
        self.search_combo = self.glade.get_widget('search_combo')
        # XXX active property set in glade file does not work
        self.search_combo.set_active(0)
        self.album_button = self.glade.get_widget('album_button')
        self.previous_button = self.glade.get_widget('previous_button')
        self.next_button = self.glade.get_widget('next_button')
        self.progressbars = [
            self.glade.get_widget('results_progressbar'),
            self.glade.get_widget('popular_progressbar'),
            self.glade.get_widget('latest_progressbar'),
        ]
        self.setup_treeviews()

        # connect signals to slots
        self.glade.signal_autoconnect({
            'on_search_button_clicked': self.on_search_button_clicked,
            'on_search_entry_activate': self.on_search_entry_activate,
            'on_notebook_switch_page': self.on_notebook_switch_page,
            'on_treeview_row_activated': self.on_treeview_row_activated,
            'on_treeview_row_clicked': self.on_treeview_row_clicked,
            'on_previous_button_clicked': self.on_previous_button_clicked,
            'on_next_button_clicked': self.on_next_button_clicked,
            'on_album_button_clicked': self.on_album_button_clicked,
            'on_cancel_button_clicked': self.on_cancel_button_clicked,
            'on_ok_button_clicked': self.on_ok_button_clicked,
        })

        self.reset()

    def activate(self, totem_object):
        """
        Plugin activation.
        """
        self.totem = totem_object
        # unparent the container to embed it into totem sidebar
        self.container.unparent()
        self.totem.add_sidebar_page("jamendo", _("Jamendo"), self.container)

    def deactivate(self, totem_object):
        """
        Plugin deactivation.
        """
        totem_object.remove_sidebar_page("jamendo")

    def create_configure_dialog(self, *args):
        """
        Plugin config dialog.
        """
        format = self.gconf.get_string('%s/format' % gconf_key)
        num_per_page = self.gconf.get_int('%s/num_per_page' % gconf_key)
        combo = self.glade.get_widget('preferred_format_combo')
        combo.set_active(self.AUDIO_FORMATS.index(format))
        spinbutton = self.glade.get_widget('album_num_spinbutton')
        spinbutton.set_value(num_per_page)
        return self.config_dialog

    def reset(self):
        """
        XXX this will be refactored asap.
        """
        self.current_page = {
            self.TAB_RESULTS: 1,
            self.TAB_POPULAR: 1,
            self.TAB_LATEST : 1
        }
        self.pages = {
            self.TAB_RESULTS: [],
            self.TAB_POPULAR: [],
            self.TAB_LATEST : []
        }
        self.album_count = [0, 0, 0]
        for tv in self.treeviews:
            tv.get_model().clear()
        self._update_buttons_state()

    def init_settings(self):
        """
        Initialize plugin settings.
        """
        format = self.gconf.get_string('%s/format' % gconf_key)
        if not format:
            format = 'ogg2'
            self.gconf.set_string('%s/format' % gconf_key, format)
        num_per_page = self.gconf.get_int('%s/num_per_page' % gconf_key)
        if not num_per_page:
            num_per_page = 10
            self.gconf.set_int('%s/num_per_page' % gconf_key, num_per_page)
        JamendoService.AUDIO_FORMAT = format
        JamendoService.NUM_PER_PAGE = num_per_page

    def setup_treeviews(self):
        """
        Setup the 3 treeview: result, popular and latest
        """
        self.treeviews = [
            self.glade.get_widget('results_treeview'),
            self.glade.get_widget('popular_treeview'),
            self.glade.get_widget('latest_treeview'),
        ]
        self.current_treeview = self.treeviews[0]
        for w in self.treeviews:

            # build a treestore
            model = gtk.TreeStore(
                gobject.TYPE_PYOBJECT, # album or track dict
                gtk.gdk.Pixbuf,        # album cover or track icon
                str,                   # album or track description
                str,                   # album or track duration
                str,                   # album or track tooltip
            )
            w.set_model(model)

            # build pixbuf column
            cell = gtk.CellRendererPixbuf()
            col = gtk.TreeViewColumn()
            col.pack_start(cell, True)
            col.set_attributes(cell, pixbuf=1)
            w.append_column(col)

            # build description column
            cell = gtk.CellRendererText()
            cell.set_property('wrap-mode', pango.WRAP_WORD)
            cell.set_property('wrap-width', 30)
            col = gtk.TreeViewColumn()
            col.pack_start(cell, True)
            col.set_attributes(cell, markup=2)
            col.set_expand(True)
            w.append_column(col)
            w.connect_after(
                'size-allocate',
                self.on_treeview_size_allocate,
                col,
                cell
            )

            # duration column
            cell = gtk.CellRendererText()
            cell.set_property('xalign', 1.0)
            cell.set_property('size-points', 8)
            col = gtk.TreeViewColumn()
            col.pack_start(cell, True)
            col.set_attributes(cell, markup=3)
            col.set_alignment(1.0)
            w.append_column(col)

            # configure the treeview
            w.set_show_expanders(False) # we manage internally expand/collapse
            w.set_tooltip_column(4)     # set the tooltip column

    def add_treeview_item(self, treeview, album):
        if not isinstance(album['image'], gtk.gdk.Pixbuf):
            # album image pixbuf is not yet built
            try:
                pb = gtk.gdk.pixbuf_new_from_file(album['image'])
                os.unlink(album['image'])
                album['image'] = pb
            except:
                # do not fail for this, just display a dummy pixbuf
                album['image'] = gtk.gdk.Pixbuf(gtk.gdk.COLORSPACE_RGB, True,
                    8, 1, 1)
        # format title
        title  = '<b>%s</b>\n' % self._format_str(album['name'])
        title += _('Artist: %s') % self._format_str(album['artist_name'])
        # format duration
        dur = self._format_duration(album['duration'])
        # format tooltip
        try:
            release = time.strptime(album['dates']['release'][0:10], '%Y-%m-%d')
            release = time.strftime('%x', release)
        except:
            release = ''
        tip = '\n'.join([
            '<b>%s</b>' % self._format_str(album['name']),
            _('Artist: %s') % self._format_str(album['artist_name']),
            _('Genre: %s') % self._format_str(album['genre']),
            _('Released on: %s') % release,
            _('License: %s') % self._format_str(album['license'][0]),
        ])
        # append album row
        parent = treeview.get_model().append(None,
            [album, album['image'], title, dur, tip]
        )
        # append track rows
        icon = gtk.gdk.Pixbuf(gtk.gdk.COLORSPACE_RGB, True, 8, 1, 1)
        for i, track in enumerate(album['tracks']):
            # track title
            tt = '<small>%02d. %s</small>' % \
                (i+1, self._format_str(track['name']))
            # track duration
            td = self._format_duration(track['duration'])
            # track tooltip
            tip = '\n'.join([
                '<b>%s</b>' %  self._format_str(track['name']),
                _('Album: %s') % self._format_str(album['name']),
                _('Artist: %s') % self._format_str(album['artist_name']),
                _('Duration: %s') % td,
            ])
            # append track
            treeview.get_model().append(parent, [track, icon, tt, td, tip])
        # update current album count
        pindex = self.treeviews.index(treeview)
        self.album_count[pindex] += 1

    def fetch_albums(self, pn=1):
        """
        Initialize the fetch thread.
        """
        tab_index = self.treeviews.index(self.current_treeview)
        if tab_index == self.TAB_POPULAR:
            params = {'order': 'rating_desc'}
        elif tab_index == self.TAB_LATEST:
            params = {'order': 'date_desc'}
        else:
            value = self.search_entry.get_text()
            if not value:
                return
            prop = self.SEARCH_CRITERIUM[self.search_combo.get_active()]
            params = {'order': 'date_desc', prop: value}
        params['pn'] = pn
        self.current_treeview.get_model().clear()
        self.previous_button.set_sensitive(False)
        self.next_button.set_sensitive(False)
        self.album_button.set_sensitive(False)
        self.progressbars[tab_index].show()
        self.progressbars[tab_index].set_fraction(0.0)
        self.progressbars[tab_index].set_text(
            _('Fetching albums, please wait...')
        )
        lcb = (self.on_fetch_albums_loop, self.current_treeview)
        dcb = (self.on_fetch_albums_done, self.current_treeview)
        ecb = (self.on_fetch_albums_error, self.current_treeview)
        thread = JamendoService(params, lcb, dcb, ecb)
        thread.start()

    def on_fetch_albums_loop(self, treeview, album):
        """
        Add an album item and its tracks to the current treeview.
        """
        self.add_treeview_item(treeview, album)
        # pulse progressbar
        pindex = self.treeviews.index(treeview)
        self.progressbars[pindex].set_fraction(
            float(self.album_count[pindex]) / float(JamendoService.NUM_PER_PAGE)
        )

    def on_fetch_albums_done(self, treeview, albums, save_state=True):
        """
        Called when the thread finished fetching albums.
        """
        pindex = self.treeviews.index(treeview)
        model = treeview.get_model()
        if save_state and len(albums):
            self.pages[pindex].append(albums)
            self.current_page[pindex] = len(self.pages[pindex])
        self._update_buttons_state()
        self.progressbars[pindex].set_fraction(0.0)
        self.progressbars[pindex].hide()
        self.album_count[pindex] = 0

    def on_fetch_albums_error(self, treeview, exc):
        """
        Called when an error occured in the thread.
        """
        self.reset()
        pindex = self.treeviews.index(treeview)
        self.progressbars[pindex].set_fraction(0.0)
        self.progressbars[pindex].hide()
        dlg = gtk.MessageDialog(
            type=gtk.MESSAGE_ERROR,
            buttons=gtk.BUTTONS_OK
        )
        dlg.set_markup(
            '<b>%s</b>' % _('An error occured while fetching albums.')
        )
        # managing exceptions with urllib is a real PITA... :(
        if hasattr(exc, 'reason'):
            try:
                reason = exc.reason[1]
            except:
                try:
                    reason = exc.reason[0]
                except:
                    reason = str(exc)
            reason = reason.capitalize()
            msg = _('Failed to connect to jamendo server.\n%s.') % reason
        elif hasattr(exc, 'code'):
            msg = _('The jamendo server returned code %s') % exc.code
        else:
            msg = str(exc)
        dlg.format_secondary_text(msg)
        dlg.run()
        dlg.destroy()

    def on_search_entry_activate(self, *args):
        """
        Called when the user typed <enter> in the search entry.
        """
        return self.on_search_button_clicked()

    def on_search_button_clicked(self, *args):
        """
        Called when the user clicked on the search button.
        """
        if not self.search_entry.get_text():
            return
        if self.current_treeview != self.treeviews[self.TAB_RESULTS]:
            self.current_treeview = self.treeviews[self.TAB_RESULTS]
            self.notebook.set_current_page(self.TAB_RESULTS)
        else:
            self.on_notebook_switch_page(new_search=True)

    def on_notebook_switch_page(self, nb=None, tab=None, tab_num=0,
        new_search=False):
        """
        Called when the changed a notebook page.
        """
        self.current_treeview = self.treeviews[int(tab_num)]
        self._update_buttons_state()
        model = self.current_treeview.get_model()
        # fetch popular and latest albums once only
        if not new_search and len(model):
            return
        if new_search:
            self.current_page[self.TAB_RESULTS] = 1
            self.pages[self.TAB_RESULTS] = []
            self.album_count[self.TAB_RESULTS] = 0
            self._update_buttons_state()
        model.clear()
        self.fetch_albums()

    def on_treeview_row_activated(self, tv, path, column):
        """
        Called when the user double-clicked on a treeview element.
        """
        sel = self._get_selection_at(0)
        try:
            prop = (len(path) == 1) and 'album_id' or 'id'
            url = '%s/stream/track/redirect/?%s=%s&streamencoding=%s' %\
                (JamendoService.API_URL, prop, sel['id'],
                 JamendoService.AUDIO_FORMAT)
            try:
                self.totem.action_set_mrl_and_play(url)
            except:
                self.totem.action_remote(totem.REMOTE_COMMAND_REPLACE, url)
            # update play icon
            empty = gtk.gdk.Pixbuf(gtk.gdk.COLORSPACE_RGB, True, 8, 1, 1)
            icon = self.window.render_icon(gtk.STOCK_MEDIA_PLAY,
                                           gtk.ICON_SIZE_MENU)
            for treeview in self.treeviews:
                model = treeview.get_model()
                for row in model:
                    if row.path == path:
                        path = (path[0], 0)
                    for subrow in row.iterchildren():
                        if subrow.path == path and treeview == tv:
                            subrow[1] = icon
                        else:
                            subrow[1] = empty
        except:
            pass

    def on_treeview_row_clicked(self, tv, evt):
        """
        Called when the user clicked on a treeview element.
        """
        try:
            coords = evt.get_coords()
            path, c, x, y = tv.get_path_at_pos(int(coords[0]), int(coords[1]))
            if (len(path) == 1):
                if tv.row_expanded(path):
                    tv.collapse_row(path)
                else:
                    tv.expand_row(path, False)
            self.album_button.set_sensitive(True)
        except:
            pass

    def on_treeview_size_allocate(self, tv, allocation, col, cell):
        """
        Hack to autowrap text of the title colum.
        """
        cols = (c for c in tv.get_columns() if c != col)
        w = allocation.width - sum(c.get_width() for c in cols)
        if cell.props.wrap_width == w or w <= 0:
            return
        cell.props.wrap_width = w

    def on_previous_button_clicked(self, *args):
        """
        Called when the user clicked the previous button.
        """
        try:
            self._update_buttons_state()
            model = self.current_treeview.get_model()
            model.clear()
            pindex = self.treeviews.index(self.current_treeview)
            self.current_page[pindex] -= 1
            albums = self.pages[pindex][self.current_page[pindex]-1]
            for album in albums:
                self.add_treeview_item(self.current_treeview, album)
            self.on_fetch_albums_done(self.current_treeview, albums, False)
        except:
            raise

    def on_next_button_clicked(self, *args):
        """
        Called when the user clicked the next button.
        """
        try:
            self._update_buttons_state()
            model = self.current_treeview.get_model()
            model.clear()
            pindex = self.treeviews.index(self.current_treeview)
            if self.current_page[pindex] == len(self.pages[pindex]):
                self.fetch_albums(self.current_page[pindex]+1)
            else:
                self.current_page[pindex] += 1
                albums = self.pages[pindex][self.current_page[pindex]-1]
                for album in albums:
                    self.add_treeview_item(self.current_treeview, album)
                self.on_fetch_albums_done(self.current_treeview, albums, False)
        except:
            raise

    def on_album_button_clicked(self, *args):
        """
        Called when the user clicked on the album button.
        """
        try:
            album_id = self._get_selection_at(0, None, True)['id']
            webbrowser.open('%s/url/album/redirect/?id=%s' % (
                (JamendoService.API_URL, album_id)
            ))
        except:
            pass

    def on_cancel_button_clicked(self, *args):
        """
        Called when the user clicked cancel in the config dialog.
        """
        self.config_dialog.hide()

    def on_ok_button_clicked(self, *args):
        """
        Called when the user clicked ok in the config dialog.
        """
        combo = self.glade.get_widget('preferred_format_combo')
        spinbutton = self.glade.get_widget('album_num_spinbutton')
        format = self.AUDIO_FORMATS[combo.get_active()]
        self.gconf.set_string('%s/format' % gconf_key, format)
        num_per_page = int(spinbutton.get_value())
        self.gconf.set_int('%s/num_per_page' % gconf_key, num_per_page)
        self.init_settings()
        self.config_dialog.hide()
        try:
            self.reset()
        except:
            pass

    def _update_buttons_state(self):
        """
        Update the state of the previous and next buttons.
        """
        sel = self.current_treeview.get_selection()
        model, it = sel.get_selected()
        pindex = self.treeviews.index(self.current_treeview)
        self.previous_button.set_sensitive(self.current_page[pindex] > 1)
        self.next_button.set_sensitive(len(model)==JamendoService.NUM_PER_PAGE)
        self.album_button.set_sensitive(it is not None)

    def _get_selection_at(self, at=0, sel=None, root=False):
        """
        Shortcut method to retrieve the value of the selected item at the
        given column.
        """
        if sel is None:
            sel = self.current_treeview.get_selection()
        model, it = sel.get_selected()
        if root:
            while model.iter_parent(it) is not None:
                it = model.iter_parent(it)
        if it is not None:
            return model.get(it, at)[0]
        return None

    def _format_str(self, st, truncate=False):
        """
        Escape entities for pango markup and force the string to utf-8.
        """
        if not st:
            return ''
        try:
            return escape(st.encode('utf8'))
        except:
            return st

    def _format_duration(self, secs):
        """
        Format the given number of seconds to a human readable duration.
        """
        try:
            secs = int(secs)
            if secs >= 3600:
                return time.strftime('%H:%M:%S', time.gmtime(secs))
            return time.strftime('%M:%S', time.gmtime(secs))
        except:
            return ''


class JamendoService(threading.Thread):
    """
    Class that requests the jamendo REST service.
    """

    API_URL = 'http://api.jamendo.com/get2'
    AUDIO_FORMAT = 'ogg2'
    NUM_PER_PAGE = 10

    def __init__(self, params, loop_cb, done_cb, error_cb):
        self.params = params
        self.loop_cb = loop_cb
        self.done_cb = done_cb
        self.error_cb = error_cb
        self.lock = threading.Lock()
        threading.Thread.__init__(self)

    def run(self):
        url = '%s/id+name+duration+image+genre+dates+artist_id+' \
              'artist_name+artist_url/album/json/?n=%s&imagesize=50' % \
              (self.API_URL, self.NUM_PER_PAGE)
        if len(self.params):
            url += '&%s' % urllib.urlencode(self.params)
        try:
            self.lock.acquire()
            albums = json.loads(self._request(url))
            ret = []
            for i, album in enumerate(albums):
                fname, headers = urllib.urlretrieve(album['image'])
                album['image'] = fname
                album['tracks'] = json.loads(self._request(
                    '%s/id+name+duration/track/json/?album_id=%s'\
                    '&order=numalbum_asc' % (self.API_URL, album['id'])
                ))
                album['license'] = json.loads(self._request(
                    '%s/name/license/json/album_license/?album_id=%s'\
                    % (self.API_URL, album['id'])
                ))
                gobject.idle_add(self.loop_cb[0], self.loop_cb[1], album)
            gobject.idle_add(self.done_cb[0], self.done_cb[1], albums)
        except Exception, exc:
            gobject.idle_add(self.error_cb[0], self.error_cb[1], exc)
        finally:
            self.lock.release()

    def _request(self, url):
        opener = urllib2.build_opener()
        opener.addheaders = [('User-agent', 'Totem Jamendo plugin')]
        handle = opener.open(url)
        data = handle.read()
        handle.close()
        return data

