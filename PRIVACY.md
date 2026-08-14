# Steltic Demo — Privacy Policy

Steltic ("the Service", "we") is operated by Steltic. This policy covers the free **Demo** of
**Steltic — the Open Source steel design assistant**, hosted at stelticai.com.

## The short version

**The Demo runs our own published example buildings only.** You cannot type a brief, upload a file,
or enter a building of your own — the controls are disabled and the server refuses anything that
isn't one of our examples. So there is no design data of yours involved, which removes the question
of what we do with it.

The one piece of your property the Demo does touch is your **AI provider API key**, and that is
covered below.

## Your AI provider and key

Steltic has no AI model of its own. When you run an example, Steltic's server calls the AI / LLM
provider you configure in Settings, authenticated with the API key you supply.

**Your key is held in server memory only, for the duration of your session. It is never written to
disk, never logged, and never sent anywhere except your chosen provider.** It is discarded when you
sign out, when the session expires, or when the server process ends — whichever comes first.

You pay your provider directly for the tokens the run consumes. We never see your billing.

## Third-party AI providers

The design is produced by the provider you select and supply. The example brief and the agent's
working conversation are sent to that provider in order to generate the design, and are processed
under **that provider's privacy terms** — please review them. Steltic is not responsible for how
your chosen provider handles data.

## Design outputs

Your run's working files (the OpenSees model, figures, report, conversation) live in a workspace
isolated to your session on **ephemeral storage that is not backed up and is cleared automatically**
when the server instance recycles. Download your package before you leave — once the session ends the
files are gone. We do not use any of it to train a model, and we do not share or sell it.

## What we record

**Usage metadata only:**

- an anonymous visitor id — a one-way salted hash of your IP address, used to enforce the Demo's
  limits (one run at a time, a few runs per day). **We do not store your IP address**, and the hash
  cannot be reversed back to it
- session start times, design-run start/stop times and durations
- run outcome (finished / paused / stopped / time limit / error)
- step and token **counts**
- the AI model and provider name you configured
- app-instance start/stop times

**We never record:** your API key, the AI's responses, the generated model or report files, or any
personal information. We collect no name, no email address and no password — the Demo has no
accounts.

We use Cloudflare Turnstile at the entrance to keep automated traffic from consuming the free
capacity. Turnstile is processed by Cloudflare under its own terms and is designed not to profile
or track visitors.

## What we don't do

We never sell your information and we don't serve ads.

## Want no third party involved at all?

Steltic is open source (MIT). Run it on your own machine with your own buildings and nothing leaves
your computer except the calls to the AI provider you choose:
**https://github.com/Steltic/steltic**

## Changes & contact

We may update this policy; the version date below reflects the latest revision.
Questions: **mikebambach@stelticai.com**

*Version 2026-08-01 · © 2026 Steltic.*
