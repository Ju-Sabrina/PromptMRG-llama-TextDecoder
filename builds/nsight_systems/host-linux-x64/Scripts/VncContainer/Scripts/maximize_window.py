# SPDX-FileCopyrightText: Copyright (c) 2022 NVIDIA CORPORATION & AFFILIATES. All rights reserved. 
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction, 
# disclosure or distribution of this material and related documentation 
# without an express license agreement from NVIDIA CORPORATION or 
# its affiliates is strictly prohibited.

import sys
import time
import traceback

import Xlib
import Xlib.display
from Xlib import X, protocol


def _send_event(root, win, ctype, data, mask=None):
    data = (data + ([0] * (5 - len(data))))[:5]
    ev = protocol.event.ClientMessage(
        window=win, client_type=ctype, data=(32, (data)))
    root.send_event(ev, event_mask=X.SubstructureRedirectMask)


def is_maximized(display, window):
    state_prop = window.get_full_property(
        display.intern_atom("_NET_WM_STATE"), Xlib.X.AnyPropertyType)
    if not state_prop:
        return False
    states = map(display.get_atom_name, state_prop.value)
    return '_NET_WM_STATE_MAXIMIZED_HORZ' in states and '_NET_WM_STATE_MAXIMIZED_VERT' in states


def resize_and_remove_decorations(width, height):
    display = Xlib.display.Display()
    root = display.screen().root
    windowID = None
    window = None
    title = None
    while True:
        try:
            windowID = root.get_full_property(display.intern_atom(
                '_NET_ACTIVE_WINDOW'), Xlib.X.AnyPropertyType).value[0]
            window = display.create_resource_object('window', windowID)
            title = window.get_wm_name()
            if len(title) == 0:
                titleQuery = window.get_full_property(
                    display.intern_atom('_NET_WM_NAME'), 0)
                if titleQuery is not None:
                    title = titleQuery.value
        except:
            traceback.print_exc()
        if window and title and title.startswith("NVIDIA Nsight Systems 202"):
            break
        print("Error occured while looking for the main window, trying to repeat...")
        time.sleep(1)
    while True:
        try:
            _MOTIF_WM_HINTS = display.intern_atom("_MOTIF_WM_HINTS")
            window.change_property(_MOTIF_WM_HINTS, _MOTIF_WM_HINTS, 32, [
                                   0x2, 0x0, 0x0, 0x0, 0x0])
            if not is_maximized(display, window):
                _send_event(root, window, display.intern_atom("_NET_WM_STATE"), [1, display.intern_atom("_NET_WM_STATE_MAXIMIZED_VERT"),
                                                                                 display.intern_atom("_NET_WM_STATE_MAXIMIZED_HORZ"), display.intern_atom("_NET_WM_STATE_ABOVE")])
            display.sync()
        except:
            print("Error occured while maximizing the main window, trying to repeat...")
            traceback.print_exc()
        time.sleep(1/2)


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        resize_and_remove_decorations(int(sys.argv[1]), int(sys.argv[2]))
    else:
        print("Width and height must exist in parameters")
