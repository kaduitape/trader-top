FROM lprett/mt5linux:mt5-installed

# O wheel Windows do MetaTrader5 importa NumPy. A imagem base traz um
# runtime C incompleto no Wine, fazendo _multiarray_umath falhar ao carregar.
RUN xvfb-run -a winetricks --force -q vcrun2022 \
    && wineserver -k \
    && wine 'C:\Python\python.exe' -m pip install \
       --no-cache-dir --force-reinstall numpy==1.26.4

