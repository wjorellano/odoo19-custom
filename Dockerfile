FROM odoo:19.0

USER root

RUN apt-get update && apt-get install -y --no-install-recommends \
   python3-dev \
   && pip3 install --no-cache-dir --break-system-packages \
   num2words \
   cryptography \
   pyOpenSSL \
   xmlsig \
   vobject \
   qrcode \
   pycountry \
   && apt-get purge -y --auto-remove \
   && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /mnt/enterprise-addons /mnt/extra-addons

COPY ./enterprise /mnt/enterprise-addons
COPY ./addons /mnt/extra-addons

RUN chown -R odoo:odoo /mnt/enterprise-addons /mnt/extra-addons

USER odoo

EXPOSE 8069