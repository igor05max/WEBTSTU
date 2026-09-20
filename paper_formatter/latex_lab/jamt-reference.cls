\NeedsTeXFormat{LaTeX2e}
\ProvidesClass{jamt-reference}[2026/09/20 JAMT reference components 2026.2]
\LoadClass[11pt,a4paper,twoside]{article}
\RequirePackage{fontspec,polyglossia}
\setdefaultlanguage{english}
\setotherlanguage{russian}
\IfFontExistsTF{Times New Roman}{
  \setmainfont{Times New Roman}\newfontfamily\cyrillicfont{Times New Roman}
}{\setmainfont{Liberation Serif}\newfontfamily\cyrillicfont{Liberation Serif}}
\RequirePackage{unicode-math}
\setmathfont{texgyretermes-math.otf}[Path=fonts/]
\RequirePackage[a4paper,left=18mm,right=18mm,top=30mm,bottom=24mm,headheight=14pt,headsep=28pt,footskip=24pt]{geometry}
\RequirePackage{microtype,multicol,needspace,graphicx,adjustbox,array,booktabs,ragged2e,xcolor,fancyhdr,xurl,hyperref}
\hypersetup{hidelinks,pdfcreator={JAMT reference typesetter},pdfproducer={XeLaTeX}}
\setlength{\columnsep}{6mm}
\setlength{\parindent}{7.5mm}
\setlength{\parskip}{0pt}
\setlength{\multicolsep}{6pt}
\raggedcolumns
\setcounter{collectmore}{-1}
\setlength{\emergencystretch}{1.5em}
\tolerance=1400
\hbadness=2500
\widowpenalty=10000
\clubpenalty=10000
\raggedbottom
\pagestyle{fancy}
\fancyhf{}
\newcommand{\jamtjournal}{Journal of Advanced Materials and Technologies.}
\newcommand{\jamtauthors}{}
\fancyhead[LE,RO]{\fontsize{10}{11.5}\selectfont\bfseries\itshape\jamtjournal}
\fancyfoot[C]{\fontsize{10}{11.5}\selectfont\itshape\jamtauthors}
\fancyfoot[LE,RO]{\fontsize{9}{10}\selectfont\bfseries\thepage}
\renewcommand{\headrulewidth}{1.8pt}
\renewcommand{\footrulewidth}{1.8pt}
\renewcommand{\headrule}{\hrule width\headwidth height .4pt\vskip .8pt\hrule width\headwidth height 1.8pt}
\renewcommand{\footrule}{\hrule width\headwidth height 1.8pt\vskip .8pt\hrule width\headwidth height .4pt\vskip 3pt}
\newlength{\labtablewidth}
\newcommand{\labtablesize}{10}
\newcommand{\labtableleading}{11.5}
\newcommand{\labfigscale}{1.0}
\newcommand{\labimage}[2]{\adjustbox{max width=\labfigscale\linewidth,max height=0.85\textheight}{\includegraphics[width=#1pt]{#2}}}
\newcommand{\jamtlicense}[2]{%
  \par\noindent\begin{minipage}{\linewidth}\hrule\vspace{6pt}%
  \begin{minipage}[c]{.17\linewidth}\centering #1\end{minipage}\hfill%
  \begin{minipage}[c]{.82\linewidth}\fontsize{9}{11}\selectfont\justifying\noindent #2\end{minipage}%
  \par\vspace{6pt}\hrule\end{minipage}\par}
\endinput
