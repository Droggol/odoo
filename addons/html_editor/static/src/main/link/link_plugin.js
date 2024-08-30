import { Plugin } from "@html_editor/plugin";
import { unwrapContents } from "@html_editor/utils/dom";
import { closestElement } from "@html_editor/utils/dom_traversal";
import { findInSelection, callbacksForCursorUpdate } from "@html_editor/utils/selection";
import { _t } from "@web/core/l10n/translation";
import { LinkPopover } from "./link_popover";
import { DIRECTIONS, leftPos, nodeSize, rightPos } from "@html_editor/utils/position";
import { prepareUpdate } from "@html_editor/utils/dom_state";
import { EMAIL_REGEX, URL_REGEX, cleanZWChars, deduceURLfromText } from "./utils";
import { isVisible } from "@html_editor/utils/dom_info";
import { KeepLast } from "@web/core/utils/concurrency";
import { rpc } from "@web/core/network/rpc";
import { memoize } from "@web/core/utils/functions";

/**
 * @typedef {import("@html_editor/core/selection_plugin").EditorSelection} EditorSelection
 */

/**
 * @param {EditorSelection} selection
 */
function isLinkActive(selection) {
    const linkElementAnchor = closestElement(selection.anchorNode, "A");
    const linkElementFocus = closestElement(selection.focusNode, "A");
    if (linkElementFocus && linkElementAnchor) {
        return linkElementAnchor === linkElementFocus;
    }
    if (linkElementAnchor || linkElementFocus) {
        return true;
    }

    return false;
}

function isSelectionHasLink(selection) {
    return findInSelection(selection, "a") ? true : false;
}

/**
 * @param { HTMLAnchorElement } link
 * @param {number} offset
 * @returns {"start"|"end"|false}
 */
function isPositionAtEdgeofLink(link, offset) {
    const childNodes = [...link.childNodes];
    let firstVisibleIndex = childNodes.findIndex(isVisible);
    firstVisibleIndex = firstVisibleIndex === -1 ? 0 : firstVisibleIndex;
    if (offset <= firstVisibleIndex) {
        return "start";
    }
    let lastVisibleIndex = childNodes.reverse().findIndex(isVisible);
    lastVisibleIndex = lastVisibleIndex === -1 ? 0 : childNodes.length - lastVisibleIndex;
    if (offset >= lastVisibleIndex) {
        return "end";
    }
    return false;
}

async function fetchExternalMetaData(url) {
    // Get the external metadata
    try {
        return await rpc("/html_editor/link_preview_external", {
            preview_url: url,
        });
    } catch {
        // when it's not possible to fetch the metadata we don't want to block the ui
        return;
    }
}

async function fetchInternalMetaData(url) {
    // Get the internal metadata
    const keepLastPromise = new KeepLast();
    const urlParsed = new URL(url);

    const result = await keepLastPromise
        .add(fetch(urlParsed))
        .then((response) => response.text())
        .then(async (content) => {
            const html_parser = new window.DOMParser();
            const doc = html_parser.parseFromString(content, "text/html");
            const internalUrlMetaData = await rpc("/html_editor/link_preview_internal", {
                preview_url: urlParsed.pathname,
            });

            internalUrlMetaData["favicon"] = doc.querySelector("link[rel~='icon']");
            internalUrlMetaData["ogTitle"] = doc.querySelector("[property='og:title']");
            internalUrlMetaData["title"] = doc.querySelector("title");

            return internalUrlMetaData;
        })
        .catch((error) => {
            // HTTP error codes should not prevent to edit the links, so we
            // only check for proper instances of Error.
            if (error instanceof Error) {
                return Promise.reject(error);
            }
        });
    return result;
}

export class LinkPlugin extends Plugin {
    static name = "link";
    static dependencies = ["dom", "selection", "split", "line_break", "overlay"];
    // @phoenix @todo: do we want to have createLink and insertLink methods in link plugin?
    static shared = ["createLink", "insertLink", "getPathAsUrlCommand"];
    /** @type { (p: LinkPlugin) => Record<string, any> } */
    static resources = (p) => ({
        toolbarCategory: {
            id: "link",
            sequence: 40,
        },
        toolbarItems: [
            {
                id: "link",
                title: _t("Link"),
                category: "link",
                action(dispatch) {
                    dispatch("CREATE_LINK_ON_SELECTION");
                },
                icon: "fa-link",
                isFormatApplied: isLinkActive,
            },
            {
                id: "unlink",
                category: "link",
                title: _t("Remove Link"),

                action(dispatch) {
                    dispatch("REMOVE_LINK_FROM_SELECTION");
                },
                icon: "fa-unlink",
                isAvailable: isSelectionHasLink,
            },
        ],

        powerboxCategory: { id: "navigation", name: _t("Navigation"), sequence: 50 },
        powerboxItems: [
            {
                name: _t("Link"),
                description: _t("Add a link"),
                category: "navigation",
                fontawesome: "fa-link",
                action(dispatch) {
                    dispatch("TOGGLE_LINK");
                },
            },
            {
                name: _t("Button"),
                description: _t("Add a button"),
                category: "navigation",
                fontawesome: "fa-link",
                action(dispatch) {
                    dispatch("TOGGLE_LINK");
                },
            },
        ],
        onSelectionChange: p.handleSelectionChange.bind(p),
        split_element_block: { callback: p.handleSplitBlock.bind(p) },
        handle_insert_line_break_element: { callback: p.handleInsertLineBreak.bind(p) },
    });
    setup() {
        this.overlay = this.shared.createOverlay(LinkPopover);
        this.addDomListener(this.editable, "click", (ev) => {
            if (ev.target.tagName === "A" && ev.target.isContentEditable) {
                ev.preventDefault();
                this.toggleLinkTools({ link: ev.target });
            }
        });
        this.addDomListener(this.editable, "keydown", (ev) => {
            if (ev.key === "Enter" || ev.key === " ") {
                this.handleAutomaticLinkInsertion();
            }
        });
        // link creation is added to the command service because of a shortcut conflict,
        // as ctrl+k is used for invoking the command palette
        this.removeLinkShortcut = this.services.command.add(
            "Create link",
            () => {
                this.toggleLinkTools();
            },
            {
                hotkey: "control+k",
                category: "shortcut_conflict",
                isAvailable: () => this.shared.getEditableSelection().inEditable,
            }
        );
        this.ignoredClasses = new Set(this.resources["link_ignore_classes"] || []);

        this.getExternalMetaData = memoize(fetchExternalMetaData);
        this.getInternalMetaData = memoize(fetchInternalMetaData);
    }

    destroy() {
        this.removeLinkShortcut();
    }

    handleCommand(command, payload) {
        switch (command) {
            case "CREATE_LINK_ON_SELECTION":
                this.toggleLinkTools(payload.options);
                break;
            case "TOGGLE_LINK":
                this.toggleLinkTools(payload.options);
                break;
            case "NORMALIZE":
                this.normalizeLink();
                break;
            case "CLEAN":
                // TODO @phoenix: evaluate if this should be cleanforsave instead
                this.removeEmptyLinks(payload.root);
                break;
            case "REMOVE_LINK_FROM_SELECTION":
                this.removeLinkFromSelection();
                break;
        }
    }

    // -------------------------------------------------------------------------
    // Commands
    // -------------------------------------------------------------------------

    /**
     * @param {string} url
     * @param {string} label
     */
    createLink(url, label) {
        const link = this.document.createElement("a");
        link.setAttribute("href", url);
        for (const [param, value] of Object.entries(this.config.defaultLinkAttributes || {})) {
            link.setAttribute(param, `${value}`);
        }
        link.innerText = label;
        return link;
    }
    /**
     * @param {string} url
     * @param {string} label
     */
    insertLink(url, label) {
        const selection = this.shared.getEditableSelection();
        let link = closestElement(selection.anchorNode, "a");
        if (link) {
            link.setAttribute("href", url);
            link.innerText = label;
        } else {
            link = this.createLink(url, label);
            this.shared.domInsert(link);
        }
        this.dispatch("ADD_STEP");
        const linkParent = link.parentElement;
        const linkOffset = Array.from(linkParent.childNodes).indexOf(link);
        this.shared.setSelection(
            { anchorNode: linkParent, anchorOffset: linkOffset + 1 },
            { normalize: false }
        );
    }
    /**
     * @param {string} text
     * @param {string} url
     */
    getPathAsUrlCommand(text, url) {
        const pasteAsURLCommand = {
            name: _t("Paste as URL"),
            description: _t("Create an URL."),
            fontawesome: "fa-link",
            action: () => {
                this.shared.domInsert(this.createLink(url, text));
                this.dispatch("ADD_STEP");
            },
        };
        return pasteAsURLCommand;
    }
    /**
     * Toggle the Link popover to edit links
     *
     * @param {Object} options
     * @param {HTMLElement} options.link
     */
    toggleLinkTools({ link } = {}) {
        if (!link) {
            link = this.getOrCreateLink();
        }
        this.linkElement = link;
    }

    normalizeLink() {
        const { anchorNode } = this.shared.getEditableSelection();
        const linkEl = closestElement(anchorNode, "a");
        if (linkEl && linkEl.isContentEditable) {
            const label = linkEl.innerText;
            const url = deduceURLfromText(label, linkEl);
            if (url) {
                linkEl.setAttribute("href", url);
            }
        }
    }

    handleSelectionChange(selection) {
        if (!selection.isCollapsed) {
            this.overlay.close();
        } else if (!selection.inEditable) {
            const selection = this.document.getSelection();
            // note that data-prevent-closing-overlay also used in color picker but link popover
            // and color picker don't open at the same time so it's ok to query like this
            const popoverEl = document.querySelector("[data-prevent-closing-overlay=true]");
            if (popoverEl?.contains(selection.anchorNode)) {
                return;
            }
            this.overlay.close();
        } else {
            const linkEl = closestElement(selection.anchorNode, "A");
            if (!linkEl) {
                this.overlay.close();
                this.removeCurrentLinkIfEmtpy();
                return;
            }
            if (linkEl !== this.linkElement) {
                this.removeCurrentLinkIfEmtpy();
                this.overlay.close();
                this.linkElement = linkEl;
            }

            const props = {
                linkEl,
                onApply: (url, label, classes) => {
                    this.linkElement.href = url;
                    if (cleanZWChars(this.linkElement.innerText) === label) {
                        this.overlay.close();
                        this.shared.setSelection(this.shared.getEditableSelection());
                    } else {
                        const restore = prepareUpdate(...leftPos(this.linkElement));
                        this.linkElement.innerText = label;
                        restore();
                        this.overlay.close();
                        this.shared.setCursorEnd(this.linkElement);
                    }
                    if (classes) {
                        this.linkElement.className = classes;
                    } else {
                        this.linkElement.removeAttribute("class");
                    }
                    this.dispatch("ADD_STEP");
                    this.removeCurrentLinkIfEmtpy();
                },
                onRemove: () => {
                    this.removeLink();
                    this.overlay.close();
                    this.dispatch("ADD_STEP");
                },
                onCopy: () => {
                    this.overlay.close();
                },
                onClose: () => {
                    this.overlay.close();
                },
                getInternalMetaData: this.getInternalMetaData,
                getExternalMetaData: this.getExternalMetaData,
            };
            // pass the link element to overlay to prevent position change
            this.overlay.open({ target: this.linkElement, props });
        }
    }

    /**
     * get the link from the selection or create one if there is none
     *
     * @return {HTMLElement}
     */
    getOrCreateLink() {
        const selection = this.shared.getEditableSelection();
        const linkElement = findInSelection(selection, "a");
        if (linkElement) {
            if (
                !linkElement.contains(selection.anchorNode) ||
                !linkElement.contains(selection.focusNode)
            ) {
                this.shared.splitSelection();
                const selectedNodes = this.shared.getSelectedNodes();
                let before = linkElement.previousSibling;
                while (before !== null && selectedNodes.includes(before)) {
                    linkElement.insertBefore(before, linkElement.firstChild);
                    before = linkElement.previousSibling;
                }
                let after = linkElement.nextSibling;
                while (after !== null && selectedNodes.includes(after)) {
                    linkElement.appendChild(after);
                    after = linkElement.nextSibling;
                }
                this.shared.setCursorEnd(linkElement);
                this.dispatch("ADD_STEP");
            }
            return linkElement;
        } else {
            // create a new link element
            const link = this.document.createElement("a");
            if (!selection.isCollapsed) {
                const content = this.shared.extractContent(selection);
                link.append(content);
            }
            this.shared.domInsert(link);
            this.shared.setCursorEnd(link);
            return link;
        }
    }

    removeCurrentLinkIfEmtpy() {
        if (this.linkElement && cleanZWChars(this.linkElement.innerText) === "") {
            this.linkElement.remove();
        }
        if (this.linkElement && !this.linkElement.href) {
            this.removeLink();
            this.dispatch("ADD_STEP");
        }
    }

    /**
     * Remove the link from the collapsed selection
     */
    removeLink() {
        const link = this.linkElement;
        const cursors = this.shared.preserveSelection();
        if (link && link.isContentEditable) {
            cursors.update(callbacksForCursorUpdate.unwrap(link));
            unwrapContents(link);
        }
        cursors.restore();
        this.linkElement = null;
    }

    removeLinkFromSelection() {
        const selection = this.shared.splitSelection();
        const cursors = this.shared.preserveSelection();

        // If not, unlink only the part(s) of the link(s) that are selected:
        // `<a>a[b</a>c<a>d</a>e<a>f]g</a>` => `<a>a</a>[bcdef]<a>g</a>`.
        let { anchorNode, focusNode, anchorOffset, focusOffset } = selection;
        const direction = selection.direction;
        // Split the links around the selection.
        const [startLink, endLink] = [
            closestElement(anchorNode, "a"),
            closestElement(focusNode, "a"),
        ];
        if (startLink) {
            anchorNode = this.shared.splitAroundUntil(anchorNode, startLink);
            anchorOffset = direction === DIRECTIONS.RIGHT ? 0 : nodeSize(anchorNode);
            this.shared.setSelection(
                { anchorNode, anchorOffset, focusNode, focusOffset },
                { normalize: true }
            );
        }
        // Only split the end link if it was not already done above.
        if (endLink && endLink.isConnected) {
            focusNode = this.shared.splitAroundUntil(focusNode, endLink);
            focusOffset = direction === DIRECTIONS.RIGHT ? nodeSize(focusNode) : 0;
            this.shared.setSelection(
                { anchorNode, anchorOffset, focusNode, focusOffset },
                { normalize: true }
            );
        }
        const targetedNodes = this.shared.getSelectedNodes();
        const links = new Set(
            targetedNodes
                .map((node) => closestElement(node, "a"))
                .filter((a) => a && a.isContentEditable)
        );
        if (links.size) {
            for (const link of links) {
                cursors.update(callbacksForCursorUpdate.unwrap(link));
                unwrapContents(link);
            }
            cursors.restore();
        }
        this.dispatch("ADD_STEP");
    }

    removeEmptyLinks(root) {
        // @todo: check for unremovables
        // @todo: preserve cursor and spaces
        for (const link of root.querySelectorAll("a")) {
            if ([...link.childNodes].some(isVisible)) {
                continue;
            }
            const classes = [...link.classList].filter((c) => !this.ignoredClasses.has(c));
            if (!classes.length) {
                link.remove();
            }
        }
    }

    /**
     * Inserts a link in the editor. Called after pressing space or (shif +) enter.
     * Performs a regex check to determine if the url has correct syntax.
     */
    handleAutomaticLinkInsertion() {
        let selection = this.shared.getEditableSelection();
        if (
            isHtmlContentSupported(selection.anchorNode) &&
            !closestElement(selection.anchorNode, "a") &&
            selection.anchorNode.nodeType === Node.TEXT_NODE
        ) {
            // Merge adjacent text nodes.
            selection.anchorNode.parentNode.normalize();
            selection = this.shared.getEditableSelection();
            const textSliced = selection.anchorNode.textContent.slice(0, selection.anchorOffset);
            const textNodeSplitted = textSliced.split(/\s/);
            const potentialUrl = textNodeSplitted.pop();
            // In case of multiple matches, only the last one will be converted.
            const match = [...potentialUrl.matchAll(new RegExp(URL_REGEX, "g"))].pop();

            if (match && !EMAIL_REGEX.test(match[0])) {
                const nodeForSelectionRestore = selection.anchorNode.splitText(
                    selection.anchorOffset
                );
                const url = match[2] ? match[0] : "http://" + match[0];
                const startOffset = selection.anchorOffset - potentialUrl.length + match.index;
                const text = selection.anchorNode.textContent.slice(
                    startOffset,
                    startOffset + match[0].length
                );
                const link = this.createLink(url, text);
                // split the text node and replace the url text with the link
                const textNodeToReplace = selection.anchorNode.splitText(startOffset);
                textNodeToReplace.splitText(match[0].length);
                selection.anchorNode.parentElement.replaceChild(link, textNodeToReplace);
                this.shared.setCursorStart(nodeForSelectionRestore);
                this.dispatch("ADD_STEP");
            }
        }
    }

    /**
     * Special behavior for links: do not break the link at its edges, but
     * rather before/after it.
     *
     * @param {Object} params
     * @param {Element} params.targetNode
     * @param {number} params.targetOffset
     * @param {Element} params.blockToSplit
     */
    handleSplitBlock(params) {
        return this.handleEnterAtEdgeOfLink(params, this.shared.splitElementBlock);
    }

    /**
     * Special behavior for links: do not add a line break at its edges, but
     * rather outside it.
     *
     * @param {Object} params
     * @param {Element} params.targetNode
     * @param {number} params.targetOffset
     */
    handleInsertLineBreak(params) {
        return this.handleEnterAtEdgeOfLink(params, this.shared.insertLineBreakElement);
    }

    /**
     * @param {Object} params
     * @param {Element} params.targetNode
     * @param {number} params.targetOffset
     * @param {Element} [params.blockToSplit]
     * @param {Function} splitOrLineBreakCallback
     */
    handleEnterAtEdgeOfLink(params, splitOrLineBreakCallback) {
        // @todo: handle target Node being a descendent of a link (iterate over
        // leaves inside the link, rather than childNodes)
        let { targetNode, targetOffset } = params;
        if (targetNode.tagName !== "A") {
            return;
        }
        const edge = isPositionAtEdgeofLink(targetNode, targetOffset);
        if (!edge) {
            return;
        }
        [targetNode, targetOffset] = edge === "start" ? leftPos(targetNode) : rightPos(targetNode);
        splitOrLineBreakCallback({ ...params, targetNode, targetOffset });
        return true;
    }
}

// @phoenix @todo: duplicate from the clipboard plugin, should be moved to a shared location
/**
 * Returns true if the provided node can suport html content.
 *
 * @param {Node} node
 * @returns {boolean}
 */
export function isHtmlContentSupported(node) {
    return !closestElement(
        node,
        '[data-oe-model]:not([data-oe-field="arch"]):not([data-oe-type="html"]),[data-oe-translation-id]',
        true
    );
}
