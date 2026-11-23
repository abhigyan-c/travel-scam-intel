# Add Travel Scam Intelligence to ChatGPT

Updated 23 November 2026. This is a custom MCP connection, not an approved public
ChatGPT directory listing. Use it only if you trust the service and its data practices.

## Connection details

| Setting | Value |
| --- | --- |
| Name | Travel Scam Intelligence |
| Description | Sourced, location-specific travel scam research and anonymous incident reporting. |
| Connection method | Public server URL |
| Server URL | `https://travel-scam-prod.tailbbf557.ts.net/mcp` |
| Authentication | No authentication / None |

You do **not** need Tailscale installed, a tailnet invitation, an API key or the
operator's login. This is a public HTTPS endpoint. Do not choose OpenAI's Secure
MCP Tunnel option: that is a different connection mechanism.

## Add and use the connection

1. Open ChatGPT on the web and go to [Plugins](https://chatgpt.com/plugins).
2. Select **+ → Add custom MCP server**.
3. Enter the name, optional description and exact server URL above, including `/mcp`.
4. Choose **No authentication**. Read the displayed warning; continue only if you
   understand and accept connecting this service.
5. Select **Create as a plugin**, inspect its discovered tools and install it.
6. Start a new chat. Type `@` and select **Travel Scam Intelligence**.

Account/workspace permissions and security restrictions can prevent adding a
custom server. If the option is absent, ask your workspace administrator; do not
assume everyone has access or that a missing older “Developer mode” toggle is an
error. These steps follow the current [OpenAI custom MCP guide](https://developers.openai.com/api/docs/guides/custom-mcp-server).

## First test: read existing results

Send this after selecting the plugin:

> Use Travel Scam Intelligence to resolve Bangkok, Thailand (city). Search existing
> findings with request_research=false. Show the sources and each finding's actual
> geographic scope. Explain confidence and missing dates. Do not start research or
> submit an incident report.

This checks the connection without requesting paid-provider research or creating
an incident report. Results may be limited or absent. A Grand Palace finding is
specific to that attraction; a country-level finding is not automatically a city
incident. Candidate confidence is not the probability that an allegation is true.

To request a researched travel report instead:

> Use Travel Scam Intelligence to check scams in Rome, Italy. I am visiting next
> month. Give me a sourced report, preserve the actual location of each finding,
> and explain research completeness. Do not submit an incident report.

Research may take time or finish with partial results. Missing results are not
permission to invent scams or a guarantee of safety. Follow the returned run's
status; repeated new requests do not make existing work faster.

## Reports, privacy and disconnecting

Researching a destination and submitting an incident are different actions. To
report an incident, ask to open the form, review/edit your text and confirm only
when ready. Text and source links are supported; the ChatGPT form does not upload
images. A receipt confirms acceptance, not verification or publication.

Do not share passwords, payment information or unnecessary personal identifiers.
Tool arguments are sent to this service; selected processing may involve Serper
and Sarvam. Accepted reports, raw objects and backups currently have no automatic
expiry. Disconnecting the plugin does not erase accepted reports. Read the
[user guide](user-guide.md) and [prepared privacy policy](../app/public/privacy.html).
Contact **abhigyanchakraborty61@gmail.com** for help or manual removal requests.

Refresh the connection after tool changes and start a new chat. Remove/disconnect
it in your plugin settings when no longer wanted. More detail is in OpenAI's
[connection and testing guide](https://developers.openai.com/plugins/deploy/connect-chatgpt).

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| Cannot reach server | Copy the exact HTTPS hostname and `/mcp` path. Ask support whether the service is healthy. Do not substitute the private Tailscale IP or the VPS IP. |
| No custom-server option | Account/workspace policy may prohibit it; contact the administrator. |
| ChatGPT answers without using the service | Select the installed plugin with `@` and explicitly ask to use it. |
| Tools or UI look outdated | Refresh the connection, then start a new conversation. |
| Research is incomplete | Read available evidence and status. Avoid repeatedly requesting new research. |
| Rate limit or temporary error | Wait and retry; capacity is shared across anonymous users. |
| Receipt lost after submitting | Keep the form open and use its exact confirmed-submission retry; do not create another report. |

## Is the Tailscale URL safe to share?

**The URL is intended to be public; sharing it does not invite someone into the
tailnet or give them SSH, database or device access.** Funnel exposes a selected
service to internet users, unlike private device sharing. Visitors do not need
a Tailscale account. See [Tailscale's distinction](https://tailscale.com/docs/reference/funnel-vs-sharing).

The hostname reveals the chosen machine/tailnet DNS labels. It contains no
password or access token. Keep Tailscale authorization links, auth keys, machine
tokens, SSH keys and admin credentials private.

For this application, Funnel points to the filtered Caddy listener on port 8080,
not the unrestricted API on port 8000. That boundary is essential: changing the
proxy target could expose private routes. A public anonymous endpoint can still
be called by anyone, including scripts; sharing it can increase spam, provider
costs and capacity pressure. Giving the link only to friends is not authentication.

### Current sharing status for the operator

On 23 November 2026, the host reported Funnel enabled and targeting
`http://127.0.0.1:8080`. Its active release was still `7d9cfec` (PR 35), not the
patched `v0.1.0` release. Do not advertise a hardened public launch based only on
the GitHub release. Deploy and verify the patched release, confirm private-route
denials and monitoring, and review residual security findings before broad
distribution. This guide does not certify the current deployment as secure.
